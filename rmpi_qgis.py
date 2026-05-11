# -*- coding: utf-8 -*-
"""
rmpi_qgis.py — Random Move Geometries Inside (QGIS Processing Algorithm)

Takes a vector layer (points, lines or polygons) where each feature belongs to a group
(identified by a selected field) and randomly moves and rotates each group within a
user-defined area.

The movement area can be defined in two ways (mutually exclusive, polygon takes precedence):
  - Rectangular extent: drawn on the map, entered manually, or taken from any loaded layer.
  - Exact polygon layer: uses the precise polygon boundary for containment checks.

Process for each group:
  1. Compute the center of gravity (cog) as the mean of feature centroids, and the
     geometric centroid (cc) as the center of the group's bounding box.
  2. Rotate all geometries by a random angle (0–360°) around the center of gravity.
  3. Calculate a random displacement that places the group inside the movement area.
  4. Apply the translation.

Containment modes:
  - Center of gravity only (default): ensures the cog lands inside the area.
    Individual geometries — especially large polygons or long lines — may partially extend outside.
  - All geometries: guarantees that no geometry goes outside the movement area.

Installation:
    Copy this file to:
      ~/.local/share/QGIS/QGIS3/profiles/default/processing/scripts/   (Linux / macOS)
      %APPDATA%\QGIS\QGIS3\profiles\default\processing\scripts\        (Windows)
    Then reload scripts in QGIS: Processing > Scripts > Reload scripts.
"""

import random

import numpy as np
from shapely import affinity
from shapely.geometry import Point, box as shapely_box
from shapely.wkt import loads as wkt_loads

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsFeatureSink,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterField,
    QgsProcessingParameterExtent,
    QgsProcessingParameterBoolean,
    QgsWkbTypes,
    QgsFeature,
    QgsGeometry,
    QgsFields,
    QgsField,
)


class RandomMoveGeometriesInside(QgsProcessingAlgorithm):

    INPUT       = 'INPUT'
    EXTENT      = 'EXTENT'
    INPUT_MARCO = 'INPUT_MARCO'
    ID_FIELD    = 'ID_FIELD'
    ALL_GEOMS   = 'ALL_GEOMS'
    OUT_FEATS   = 'OUT_FEATS'
    OUT_CG      = 'OUT_CG'
    OUT_CC      = 'OUT_CC'
    OUT_CG_ORIG = 'OUT_CG_ORIG'
    OUT_CC_ORIG = 'OUT_CC_ORIG'

    MAX_TRIES = 100

    # ------------------------------------------------------------------
    # Algorithm metadata
    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return RandomMoveGeometriesInside()

    def name(self):
        return 'randommovegeometriesinside'

    def displayName(self):
        return self.tr('Random Move Geometries Inside')

    def group(self):
        return self.tr('Digdgeo')

    def groupId(self):
        return 'digdgeo'

    def shortHelpString(self):
        return self.tr(
            'Randomly moves and rotates groups of geometries (points, lines or polygons) '
            'within a user-defined area. The geometry type is detected automatically.\n\n'
            'Movement area — choose one:\n'
            '• Rectangular extent: draw on the map, enter coordinates, or use any layer\'s extent.\n'
            '• Exact polygon layer: uses the precise polygon boundary. '
            'If provided, it overrides the extent.\n\n'
            'Containment modes:\n'
            '• Center of gravity only (default): the center of gravity lands inside the area. '
            'Parts of large polygons or long lines may extend outside.\n'
            '• All geometries: guarantees every geometry stays fully within the area. '
            'Recommended for polygon layers (e.g. home ranges). '
            'Groups larger than the area are not moved and a warning is raised.\n\n'
            'Outputs:\n'
            '• Moved and rotated geometries (original attributes preserved)\n'
            '• Original and displaced centers of gravity\n'
            '• Original and displaced geometric centroids'
        )

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr('Input layer (points, lines or polygons)'),
                [QgsProcessing.TypeVectorAnyGeometry]
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.EXTENT,
                self.tr('Movement area — rectangular extent')
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_MARCO,
                self.tr('Movement area — exact polygon layer (overrides extent if set)'),
                [QgsProcessing.TypeVectorPolygon],
                optional=True
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.ID_FIELD,
                self.tr('Group identifier field'),
                parentLayerParameterName=self.INPUT,
                defaultValue='ID_progres'
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ALL_GEOMS,
                self.tr('Ensure all geometries stay within the movement area'),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_FEATS,
                self.tr('Moved and rotated geometries'),
                type=QgsProcessing.TypeVectorAnyGeometry
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_CG,
                self.tr('Displaced centers of gravity'),
                type=QgsProcessing.TypeVectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_CC,
                self.tr('Displaced geometric centroids'),
                type=QgsProcessing.TypeVectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_CG_ORIG,
                self.tr('Original centers of gravity'),
                type=QgsProcessing.TypeVectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_CC_ORIG,
                self.tr('Original geometric centroids'),
                type=QgsProcessing.TypeVectorPoint
            )
        )

    # ------------------------------------------------------------------
    # Main logic
    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        source    = self.parameterAsSource(parameters, self.INPUT, context)
        id_field  = self.parameterAsString(parameters, self.ID_FIELD, context)
        all_geoms = self.parameterAsBoolean(parameters, self.ALL_GEOMS, context)

        # Geometry type detection
        wkb_type  = source.wkbType()
        geom_type = QgsWkbTypes.geometryType(wkb_type)
        type_label = {
            QgsWkbTypes.PointGeometry:   'Point',
            QgsWkbTypes.LineGeometry:    'Line',
            QgsWkbTypes.PolygonGeometry: 'Polygon',
        }.get(geom_type, 'Unknown')

        mode = 'all geometries' if all_geoms else 'center of gravity only'
        feedback.pushInfo(f'Geometry type: {type_label} | Containment mode: {mode}')

        # Rectangular extent (always required)
        extent = self.parameterAsExtent(parameters, self.EXTENT, context,
                                        source.sourceCrs())
        minx, miny = extent.xMinimum(), extent.yMinimum()
        maxx, maxy = extent.xMaximum(), extent.yMaximum()

        # Exact polygon layer (optional) — overrides extent if provided
        marco_source = self.parameterAsSource(parameters, self.INPUT_MARCO, context)
        if marco_source is not None and marco_source.featureCount() > 0:
            marco_feat = next(marco_source.getFeatures())
            marco_geom = wkt_loads(marco_feat.geometry().asWkt())
            if marco_geom.geom_type == 'MultiPolygon':
                marco_geom = max(marco_geom.geoms, key=lambda p: p.area)
            marco_poly = marco_geom
            minx, miny, maxx, maxy = marco_poly.bounds
            is_rect = False
            feedback.pushInfo('Using exact polygon boundary for containment.')
        else:
            marco_poly = shapely_box(minx, miny, maxx, maxy)
            is_rect = True
            feedback.pushInfo(
                f'Using rectangular extent: X [{minx:.1f} – {maxx:.1f}]  '
                f'Y [{miny:.1f} – {maxy:.1f}]'
            )

        # Field schemas
        fields_in   = source.fields()
        crs         = source.sourceCrs()

        fields_cg = QgsFields()
        fields_cg.append(QgsField('id',     QVariant.String))
        fields_cg.append(QgsField('angle',  QVariant.Int))
        fields_cg.append(QgsField('xoff',   QVariant.Double))
        fields_cg.append(QgsField('yoff',   QVariant.Double))

        fields_orig = QgsFields()
        fields_orig.append(QgsField('id', QVariant.String))

        (sink_feats,   dest_feats)   = self.parameterAsSink(parameters, self.OUT_FEATS,   context, fields_in,   wkb_type,          crs)
        (sink_cg,      dest_cg)      = self.parameterAsSink(parameters, self.OUT_CG,      context, fields_cg,   QgsWkbTypes.Point, crs)
        (sink_cc,      dest_cc)      = self.parameterAsSink(parameters, self.OUT_CC,      context, fields_cg,   QgsWkbTypes.Point, crs)
        (sink_cg_orig, dest_cg_orig) = self.parameterAsSink(parameters, self.OUT_CG_ORIG, context, fields_orig, QgsWkbTypes.Point, crs)
        (sink_cc_orig, dest_cc_orig) = self.parameterAsSink(parameters, self.OUT_CC_ORIG, context, fields_orig, QgsWkbTypes.Point, crs)

        # Group features by ID
        grupos = {}
        for feat in source.getFeatures():
            gid = feat[id_field]
            grupos.setdefault(gid, []).append(feat)

        total = len(grupos)
        feedback.pushInfo(f'Found {total} groups.')

        # Main processing loop
        for idx, (gid, feats) in enumerate(grupos.items()):
            if feedback.isCanceled():
                break

            feedback.setProgress(int(idx / total * 100))
            feedback.pushInfo(f'Processing group {gid}...')

            # Centroids (works for points, lines and polygons)
            centroids = [self._feature_centroid(f, geom_type) for f in feats]
            xs = [c[0] for c in centroids]
            ys = [c[1] for c in centroids]

            cog = (float(np.mean(xs)), float(np.mean(ys)))
            cc  = ((max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0)
            ang = random.randint(0, 360)

            # Write original centroids
            self._write_orig(sink_cg_orig, fields_orig, gid, Point(cog))
            self._write_orig(sink_cc_orig, fields_orig, gid, Point(cc))

            # Convert to shapely and rotate around the center of gravity
            shapely_geoms = [wkt_loads(f.geometry().asWkt()) for f in feats]
            rotated       = [affinity.rotate(g, ang, origin=cog) for g in shapely_geoms]

            # Compute displacement
            if all_geoms:
                xoff, yoff = self._displace_all_geoms(
                    rotated, marco_poly, minx, miny, maxx, maxy, is_rect, gid, feedback
                )
            else:
                xoff, yoff = self._displace_cog(
                    cog, marco_poly, minx, miny, maxx, maxy, gid, feedback
                )

            # Translate and write features
            for feat, rg in zip(feats, rotated):
                rgt = affinity.translate(rg, xoff, yoff)
                out = QgsFeature(fields_in)
                out.setAttributes(feat.attributes())
                out.setGeometry(QgsGeometry.fromWkt(rgt.wkt))
                sink_feats.addFeature(out, QgsFeatureSink.FastInsert)

            # Write displaced centroids
            cog_moved = affinity.translate(Point(cog), xoff, yoff)
            cc_moved  = affinity.translate(Point(cc),  xoff, yoff)
            self._write_point(sink_cg, fields_cg, gid, ang, xoff, yoff, cog_moved)
            self._write_point(sink_cc, fields_cg, gid, ang, xoff, yoff, cc_moved)

        return {
            self.OUT_FEATS:   dest_feats,
            self.OUT_CG:      dest_cg,
            self.OUT_CC:      dest_cc,
            self.OUT_CG_ORIG: dest_cg_orig,
            self.OUT_CC_ORIG: dest_cc_orig,
        }

    # ------------------------------------------------------------------
    # Displacement methods
    # ------------------------------------------------------------------
    def _displace_cog(self, cog, marco_poly, minx, miny, maxx, maxy, gid, feedback):
        """
        Random quadrant-based displacement: only the center of gravity
        needs to land inside the movement area. Up to MAX_TRIES attempts.
        """
        corners = {
            'NW': (minx, maxy), 'NE': (maxx, maxy),
            'SW': (minx, miny), 'SE': (maxx, miny),
        }
        for _ in range(self.MAX_TRIES):
            quadrant  = random.choice(list(corners.keys()))
            cx, cy    = corners[quadrant]
            dx, dy    = cx - cog[0], cy - cog[1]
            xoff = round(random.uniform(min(0, dx), max(0, dx)), 2)
            yoff = round(random.uniform(min(0, dy), max(0, dy)), 2)
            if marco_poly.contains(affinity.translate(Point(cog), xoff, yoff)):
                feedback.pushInfo(f'  Group {gid} → {quadrant} (dx={xoff:.1f}, dy={yoff:.1f})')
                return xoff, yoff

        feedback.pushWarning(
            f'Could not find a valid displacement for group {gid} '
            f'after {self.MAX_TRIES} attempts. Group will not be moved.'
        )
        return 0.0, 0.0

    def _displace_all_geoms(self, rotated, marco_poly, minx, miny, maxx, maxy,
                            is_rect, gid, feedback):
        """
        Computes a displacement that guarantees all rotated geometries stay
        within the movement area.

        - Rectangular extent: derives the valid displacement range analytically
          from the rotated group's bounding box. Single random draw, no retries.
        - Exact polygon: samples within the polygon's bounding box and checks
          containment of the rotated group's bbox. Up to MAX_TRIES attempts.
        """
        all_bounds = [g.bounds for g in rotated]
        rx_min = min(b[0] for b in all_bounds)
        ry_min = min(b[1] for b in all_bounds)
        rx_max = max(b[2] for b in all_bounds)
        ry_max = max(b[3] for b in all_bounds)

        xoff_min, xoff_max = minx - rx_min, maxx - rx_max
        yoff_min, yoff_max = miny - ry_min, maxy - ry_max

        if xoff_min > xoff_max or yoff_min > yoff_max:
            feedback.pushWarning(
                f'Group {gid} is larger than the movement area after rotation. '
                f'Group will not be moved.'
            )
            return 0.0, 0.0

        if is_rect:
            xoff = round(random.uniform(xoff_min, xoff_max), 2)
            yoff = round(random.uniform(yoff_min, yoff_max), 2)
            feedback.pushInfo(f'  Group {gid}: dx={xoff:.1f} dy={yoff:.1f}')
            return xoff, yoff

        # Exact polygon: check that the rotated group's bbox fits inside
        rot_bbox = shapely_box(rx_min, ry_min, rx_max, ry_max)
        for _ in range(self.MAX_TRIES):
            xoff = round(random.uniform(xoff_min, xoff_max), 2)
            yoff = round(random.uniform(yoff_min, yoff_max), 2)
            if marco_poly.contains(affinity.translate(rot_bbox, xoff, yoff)):
                feedback.pushInfo(f'  Group {gid}: dx={xoff:.1f} dy={yoff:.1f}')
                return xoff, yoff

        feedback.pushWarning(
            f'Could not find a valid displacement for group {gid} '
            f'after {self.MAX_TRIES} attempts. Group will not be moved.'
        )
        return 0.0, 0.0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _feature_centroid(self, feat, geom_type):
        """Returns (x, y) centroid of a feature regardless of geometry type."""
        if geom_type == QgsWkbTypes.PointGeometry:
            pt = feat.geometry().asPoint()
        else:
            pt = feat.geometry().centroid().asPoint()
        return (pt.x(), pt.y())

    def _write_point(self, sink, fields, gid, angle, xoff, yoff, point):
        feat = QgsFeature(fields)
        feat.setAttributes([str(gid), int(angle), float(xoff), float(yoff)])
        feat.setGeometry(QgsGeometry.fromWkt(point.wkt))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)

    def _write_orig(self, sink, fields, gid, point):
        feat = QgsFeature(fields)
        feat.setAttributes([str(gid)])
        feat.setGeometry(QgsGeometry.fromWkt(point.wkt))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)
