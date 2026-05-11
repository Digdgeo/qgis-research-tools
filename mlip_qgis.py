# -*- coding: utf-8 -*-
"""
mlip_qgis.py — Max Line Inside Polygon (QGIS Processing Algorithm)

For each polygon in the input layer, computes:
  1. Longest interior line  : longest diagonal completely inside the polygon (vertex to vertex).
  2. Longest exterior line  : longest diagonal that intersects but is not fully inside.
  3. Mid-point perpendicular: perpendicular to the interior line at its midpoint, clipped to the polygon.
  4. Maximum perpendicular  : the parallel to the above with the greatest length inside the polygon.

Installation:
    Copy this file to:
      ~/.local/share/QGIS/QGIS3/profiles/default/processing/scripts/   (Linux / macOS)
      %APPDATA%\QGIS\QGIS3\profiles\default\processing\scripts\        (Windows)
    Then reload scripts in QGIS: Processing > Scripts > Reload scripts.
"""

import math

from qgis.PyQt.QtCore import QCoreApplication, QVariant
from qgis.core import (
    QgsProcessing,
    QgsFeatureSink,
    QgsProcessingAlgorithm,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterBoolean,
    QgsWkbTypes,
    QgsFeature,
    QgsGeometry,
    QgsFields,
    QgsField,
)
from shapely.geometry import Point, LineString
from shapely.wkt import loads as wkt_loads


class MaxLineInsidePolygon(QgsProcessingAlgorithm):

    INPUT        = 'INPUT'
    CALC_EXT     = 'CALC_EXT'
    CALC_PERP    = 'CALC_PERP'
    CALC_MAXPERP = 'CALC_MAXPERP'
    OUT_INT      = 'OUT_INT'
    OUT_EXT      = 'OUT_EXT'
    OUT_PERP     = 'OUT_PERP'
    OUT_MAXPERP  = 'OUT_MAXPERP'

    # ------------------------------------------------------------------
    # Algorithm metadata
    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return MaxLineInsidePolygon()

    def name(self):
        return 'maxlineinsidepolygon'

    def displayName(self):
        return self.tr('Max Line Inside Polygon')

    def group(self):
        return self.tr('Digdgeo')

    def groupId(self):
        return 'digdgeo'

    def shortHelpString(self):
        return self.tr(
            'Computes the most significant lines for each polygon in the input layer.\n\n'
            'Outputs:\n'
            '• Interior line: longest diagonal completely contained within the polygon.\n'
            '• Exterior line: longest diagonal between vertices that intersects the polygon.\n'
            '• Mid-point perpendicular: perpendicular to the interior line at its midpoint.\n'
            '• Maximum perpendicular: the widest parallel to the above that fits inside the polygon.\n\n'
            'Note: the algorithm uses a brute-force O(n²) approach over polygon vertices. '
            'For highly detailed polygons, consider simplifying the geometry beforehand.'
        )

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr('Polygon layer'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CALC_EXT,
                self.tr('Calculate longest exterior line'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CALC_PERP,
                self.tr('Calculate mid-point perpendicular'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CALC_MAXPERP,
                self.tr('Calculate maximum perpendicular'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_INT,
                self.tr('Interior lines'),
                type=QgsProcessing.TypeVectorLine
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_EXT,
                self.tr('Exterior lines'),
                type=QgsProcessing.TypeVectorLine
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_PERP,
                self.tr('Mid-point perpendicular'),
                type=QgsProcessing.TypeVectorLine
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_MAXPERP,
                self.tr('Maximum perpendicular'),
                type=QgsProcessing.TypeVectorLine
            )
        )

    # ------------------------------------------------------------------
    # Main logic
    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        source       = self.parameterAsSource(parameters, self.INPUT, context)
        calc_ext     = self.parameterAsBoolean(parameters, self.CALC_EXT, context)
        calc_perp    = self.parameterAsBoolean(parameters, self.CALC_PERP, context)
        calc_maxperp = self.parameterAsBoolean(parameters, self.CALC_MAXPERP, context)

        fields = QgsFields()
        fields.append(QgsField('id',     QVariant.Int))
        fields.append(QgsField('length', QVariant.Double))

        crs = source.sourceCrs()

        (sink_int,     dest_int)     = self.parameterAsSink(parameters, self.OUT_INT,     context, fields, QgsWkbTypes.LineString, crs)
        (sink_ext,     dest_ext)     = self.parameterAsSink(parameters, self.OUT_EXT,     context, fields, QgsWkbTypes.LineString, crs)
        (sink_perp,    dest_perp)    = self.parameterAsSink(parameters, self.OUT_PERP,    context, fields, QgsWkbTypes.LineString, crs)
        (sink_maxperp, dest_maxperp) = self.parameterAsSink(parameters, self.OUT_MAXPERP, context, fields, QgsWkbTypes.LineString, crs)

        # --- Read polygons -------------------------------------------------------
        features = list(source.getFeatures())
        total    = len(features)

        dpg = {}  # {fid: shapely Polygon}
        d   = {}  # {fid: [shapely Point, ...]}  exterior ring vertices

        for feat in features:
            if feedback.isCanceled():
                break
            fid  = feat.id()
            geom = wkt_loads(feat.geometry().asWkt())

            # MultiPolygon: use the largest part
            if geom.geom_type == 'MultiPolygon':
                poly = max(geom.geoms, key=lambda p: p.area)
            else:
                poly = geom

            dpg[fid] = poly
            d[fid]   = [Point(c) for c in poly.exterior.coords]

        # --- Find longest lines (brute-force O(n²)) ------------------------------
        din  = {}  # {fid: LineString}  longest interior line
        dout = {}  # {fid: LineString}  longest exterior line

        for idx, (fid, vertices) in enumerate(d.items()):
            if feedback.isCanceled():
                break

            feedback.setProgress(int(idx / total * 50))
            feedback.pushInfo(self.tr(f'Processing polygon {fid} ({len(vertices)} vertices)...'))

            m_in  = 0.0
            m_out = 0.0
            poly  = dpg[fid]

            for n, e in enumerate(vertices):
                for nn, ee in enumerate(vertices):
                    if nn <= n:
                        continue

                    dist = e.distance(ee)

                    if dist > m_in:
                        line = LineString([(e.x, e.y), (ee.x, ee.y)])

                        if line.within(poly):
                            din[fid] = line
                            m_in = dist
                        elif calc_ext and line.intersects(poly) and dist > m_out:
                            dout[fid] = line
                            m_out = dist

        # --- Write interior and exterior lines -----------------------------------
        for fid, line in din.items():
            self._write_line(sink_int, fields, fid, line)

        if calc_ext:
            for fid, line in dout.items():
                self._write_line(sink_ext, fields, fid, line)

        # --- Perpendiculars ------------------------------------------------------
        if calc_perp:
            dperp    = {}
            max_perp = {}

            for idx, (fid, v) in enumerate(din.items()):
                if feedback.isCanceled():
                    break

                feedback.setProgress(50 + int(idx / max(len(din), 1) * 50))
                feedback.pushInfo(self.tr(f'Computing perpendiculars for polygon {fid}...'))

                mid = v.interpolate(0.5, normalized=True)

                pt1, pt2 = list(v.coords)[0], list(v.coords)[1]
                bearing  = math.degrees(math.atan2(pt2[1] - pt1[1], pt2[0] - pt1[0]))

                ang1 = math.radians(bearing - 90)
                ang2 = math.radians(bearing + 90)

                p1 = Point(mid.x + v.length * math.cos(ang1),
                           mid.y + v.length * math.sin(ang1))
                p2 = Point(mid.x + v.length * math.cos(ang2),
                           mid.y + v.length * math.sin(ang2))

                perpline     = LineString((p1, p2))
                intersection = perpline.intersection(dpg[fid])

                if not intersection.is_empty:
                    dperp[fid] = intersection

                if calc_maxperp:
                    md   = 0.0
                    step = v.length / 100.0

                    for i in range(-50, 51):
                        try:
                            offset = step * i
                            mperp = perpline.parallel_offset(abs(offset),
                                                             side='left' if offset >= 0 else 'right')
                            mint  = mperp.intersection(dpg[fid])
                            if mint.length > md:
                                max_perp[fid] = mint
                                md = mint.length
                        except Exception:
                            pass

            if calc_perp:
                for fid, line in dperp.items():
                    self._write_line(sink_perp, fields, fid, line)

            if calc_maxperp:
                for fid, line in max_perp.items():
                    self._write_line(sink_maxperp, fields, fid, line)

        return {
            self.OUT_INT:     dest_int,
            self.OUT_EXT:     dest_ext,
            self.OUT_PERP:    dest_perp,
            self.OUT_MAXPERP: dest_maxperp,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _write_line(self, sink, fields, fid, geom):
        """Write a shapely geometry as a QgsFeature to a sink."""
        if geom is None or geom.is_empty:
            return
        feat = QgsFeature(fields)
        feat.setAttributes([int(fid), float(geom.length)])
        feat.setGeometry(QgsGeometry.fromWkt(geom.wkt))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)
