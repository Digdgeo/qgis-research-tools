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
    CALC_MORPH   = 'CALC_MORPH'
    OUT_INT      = 'OUT_INT'
    OUT_EXT      = 'OUT_EXT'
    OUT_PERP     = 'OUT_PERP'
    OUT_MAXPERP  = 'OUT_MAXPERP'
    OUT_MORPH    = 'OUT_MORPH'

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
            'Computes the most significant lines and shape (morphometric) parameters '
            'for each polygon in the input layer.\n\n'
            'Line outputs:\n'
            '• Interior line: longest diagonal completely contained within the polygon.\n'
            '• Exterior line: longest diagonal between vertices that intersects the polygon.\n'
            '• Mid-point perpendicular: perpendicular to the interior line at its midpoint.\n'
            '• Maximum perpendicular: the widest parallel to the above that fits inside the polygon.\n\n'
            'Morphometrics (summary polygon layer, one feature per input polygon):\n'
            '• orient: dominant orientation (0–180°) from the minimum rotated rectangle.\n'
            '• major_azim: azimuth (0–180°) of the interior line (major axis).\n'
            '• mid_width / max_width: width at the midpoint and maximum width.\n'
            '• elongation: major axis length / maximum width.\n'
            '• compact: Polsby-Popper compactness 4·π·A / P² (1 = circle).\n'
            '• rectang: rectangularity, area / minimum rotated rectangle area (1 = rectangle).\n'
            '• convex: convexity, area / convex hull area (<1 with concavities/embayments).\n'
            '• shape_idx: shape/crenulation index P / (2·√(π·A)) (1 = circle, grows with irregularity).\n\n'
            'The morphometric attributes are also copied onto the interior line layer.\n\n'
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
            QgsProcessingParameterBoolean(
                self.CALC_MORPH,
                self.tr('Calculate morphometric parameters (summary polygon layer)'),
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
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_MORPH,
                self.tr('Morphometrics (summary polygons)'),
                type=QgsProcessing.TypeVectorPolygon
            )
        )

    # ------------------------------------------------------------------
    # Main logic
    # ------------------------------------------------------------------
    # Morphometric fields shared by the interior line and the summary layer.
    _METRIC_FIELDS = [
        ('orient',     QVariant.Double),
        ('mid_width',  QVariant.Double),
        ('max_width',  QVariant.Double),
        ('elongation', QVariant.Double),
        ('compact',    QVariant.Double),
        ('rectang',    QVariant.Double),
        ('convex',     QVariant.Double),
        ('shape_idx',  QVariant.Double),
    ]

    def processAlgorithm(self, parameters, context, feedback):
        source       = self.parameterAsSource(parameters, self.INPUT, context)
        calc_ext     = self.parameterAsBoolean(parameters, self.CALC_EXT, context)
        calc_perp    = self.parameterAsBoolean(parameters, self.CALC_PERP, context)
        calc_maxperp = self.parameterAsBoolean(parameters, self.CALC_MAXPERP, context)
        calc_morph   = self.parameterAsBoolean(parameters, self.CALC_MORPH, context)

        # Simple fields (id, length) for the exterior/perpendicular line layers.
        fields = QgsFields()
        fields.append(QgsField('id',     QVariant.Int))
        fields.append(QgsField('length', QVariant.Double))

        # Interior line carries the morphometrics too (length == major axis length,
        # azimuth == major axis azimuth).
        fields_int = QgsFields()
        fields_int.append(QgsField('id',      QVariant.Int))
        fields_int.append(QgsField('length',  QVariant.Double))
        fields_int.append(QgsField('azimuth', QVariant.Double))
        for nm, tp in self._METRIC_FIELDS:
            fields_int.append(QgsField(nm, tp))

        # Summary polygon layer: one feature per input polygon.
        fields_morph = QgsFields()
        fields_morph.append(QgsField('id',         QVariant.Int))
        fields_morph.append(QgsField('area',       QVariant.Double))
        fields_morph.append(QgsField('perimeter',  QVariant.Double))
        fields_morph.append(QgsField('major_len',  QVariant.Double))
        fields_morph.append(QgsField('major_azim', QVariant.Double))
        for nm, tp in self._METRIC_FIELDS:
            fields_morph.append(QgsField(nm, tp))

        crs = source.sourceCrs()

        (sink_int,     dest_int)     = self.parameterAsSink(parameters, self.OUT_INT,     context, fields_int,   QgsWkbTypes.LineString, crs)
        (sink_ext,     dest_ext)     = self.parameterAsSink(parameters, self.OUT_EXT,     context, fields,       QgsWkbTypes.LineString, crs)
        (sink_perp,    dest_perp)    = self.parameterAsSink(parameters, self.OUT_PERP,    context, fields,       QgsWkbTypes.LineString, crs)
        (sink_maxperp, dest_maxperp) = self.parameterAsSink(parameters, self.OUT_MAXPERP, context, fields,       QgsWkbTypes.LineString, crs)
        (sink_morph,   dest_morph)   = self.parameterAsSink(parameters, self.OUT_MORPH,   context, fields_morph, QgsWkbTypes.Polygon,    crs)

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

        # --- Perpendiculars ------------------------------------------------------
        dperp    = {}  # {fid: geometry}  mid-point perpendicular clipped to polygon
        max_perp = {}  # {fid: geometry}  widest parallel inside the polygon

        if calc_perp:
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

        # --- Morphometric parameters ---------------------------------------------
        metrics = {}  # {fid: {metric_name: value}}
        for fid, poly in dpg.items():
            if feedback.isCanceled():
                break

            area  = poly.area
            perim = poly.length

            # Major axis (interior line): length and azimuth folded to 0–180°.
            if fid in din:
                c = list(din[fid].coords)
                major_len  = din[fid].length
                major_azim = self._azimuth(c[0][0], c[0][1], c[1][0], c[1][1])
            else:
                major_len  = None
                major_azim = None

            # Widths from the perpendiculars (total length of the clipped section).
            mid_width = dperp[fid].length    if fid in dperp    else None
            max_width = max_perp[fid].length if fid in max_perp else None

            # Elongation: major axis length / maximum width (fall back to mid width).
            width_ref = max_width if max_width else mid_width
            elongation = (major_len / width_ref) if (major_len and width_ref) else None

            metrics[fid] = {
                'area':       area,
                'perimeter':  perim,
                'major_len':  major_len,
                'major_azim': major_azim,
                'orient':     self._mrr_orientation(poly),
                'mid_width':  mid_width,
                'max_width':  max_width,
                'elongation': elongation,
                'compact':    (4.0 * math.pi * area / (perim ** 2)) if perim > 0 else None,
                'rectang':    self._rectangularity(poly, area),
                'convex':     (area / poly.convex_hull.area) if poly.convex_hull.area > 0 else None,
                'shape_idx':  (perim / (2.0 * math.sqrt(math.pi * area))) if area > 0 else None,
            }

        # --- Write line layers ---------------------------------------------------
        for fid, line in din.items():
            m = metrics.get(fid, {})
            attrs = [int(fid), float(line.length), m.get('major_azim')]
            attrs += [m.get(nm) for nm, _ in self._METRIC_FIELDS]
            self._write_geom(sink_int, fields_int, attrs, line)

        if calc_ext:
            for fid, line in dout.items():
                self._write_line(sink_ext, fields, fid, line)

        if calc_perp:
            for fid, line in dperp.items():
                self._write_line(sink_perp, fields, fid, line)

        if calc_maxperp:
            for fid, line in max_perp.items():
                self._write_line(sink_maxperp, fields, fid, line)

        # --- Write morphometric summary layer ------------------------------------
        if calc_morph:
            for fid, poly in dpg.items():
                m = metrics[fid]
                attrs = [int(fid), m['area'], m['perimeter'], m['major_len'], m['major_azim']]
                attrs += [m.get(nm) for nm, _ in self._METRIC_FIELDS]
                self._write_geom(sink_morph, fields_morph, attrs, poly)

        return {
            self.OUT_INT:     dest_int,
            self.OUT_EXT:     dest_ext,
            self.OUT_PERP:    dest_perp,
            self.OUT_MAXPERP: dest_maxperp,
            self.OUT_MORPH:   dest_morph,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _azimuth(self, x1, y1, x2, y2):
        """Compass azimuth of the segment (from North, clockwise), folded to 0–180°."""
        return math.degrees(math.atan2(x2 - x1, y2 - y1)) % 180.0

    def _mrr_orientation(self, poly):
        """Dominant orientation (0–180°) from the longest side of the minimum
        rotated rectangle. Robust against isolated vertices."""
        try:
            coords = list(poly.minimum_rotated_rectangle.exterior.coords)
        except Exception:
            return None
        if len(coords) < 4:
            return None
        best_len = -1.0
        best     = None
        for i in range(len(coords) - 1):
            (x1, y1), (x2, y2) = coords[i], coords[i + 1]
            L = math.hypot(x2 - x1, y2 - y1)
            if L > best_len:
                best_len = L
                best     = (x1, y1, x2, y2)
        return self._azimuth(*best) if best else None

    def _rectangularity(self, poly, area):
        """Area divided by the area of the minimum rotated rectangle (1 = rectangle)."""
        try:
            rect_area = poly.minimum_rotated_rectangle.area
        except Exception:
            return None
        return (area / rect_area) if rect_area > 0 else None

    def _write_line(self, sink, fields, fid, geom):
        """Write a shapely geometry as a QgsFeature (id, length) to a sink."""
        if geom is None or geom.is_empty:
            return
        feat = QgsFeature(fields)
        feat.setAttributes([int(fid), float(geom.length)])
        feat.setGeometry(QgsGeometry.fromWkt(geom.wkt))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)

    def _write_geom(self, sink, fields, attrs, geom):
        """Write a shapely geometry with an explicit attribute list to a sink."""
        if geom is None or geom.is_empty:
            return
        feat = QgsFeature(fields)
        feat.setAttributes(attrs)
        feat.setGeometry(QgsGeometry.fromWkt(geom.wkt))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)
