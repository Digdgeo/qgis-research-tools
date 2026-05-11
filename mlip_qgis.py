# -*- coding: utf-8 -*-
"""
mlip_qgis.py — Max Line Inside Polygon (QGIS Processing Algorithm)

Para cada polígono de la capa de entrada calcula:
  1. Línea interior más larga   : diagonal más larga completamente dentro del polígono.
  2. Línea exterior más larga   : diagonal más larga que intersecta (pero no está dentro).
  3. Perpendicular en el punto medio de la línea interior más larga.
  4. Perpendicular máxima       : la paralela a la anterior con mayor longitud dentro del polígono.

Instalación:
    Copia este archivo en:
      ~/.local/share/QGIS/QGIS3/profiles/default/processing/scripts/
    y recarga los scripts en QGIS (Processing > Scripts > Recargar scripts).
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
    # Metadatos del algoritmo
    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return MaxLineInsidePolygon()

    def name(self):
        return 'maxlineinsidepolygon'

    def displayName(self):
        return self.tr('Líneas máximas dentro de polígonos')

    def group(self):
        return self.tr('Digdgeo')

    def groupId(self):
        return 'digdgeo'

    def shortHelpString(self):
        return self.tr(
            'Calcula las líneas más significativas dentro de cada polígono.\n\n'
            'Salidas:\n'
            '• Línea interior: diagonal más larga completamente dentro del polígono.\n'
            '• Línea exterior: diagonal más larga que intersecta el polígono.\n'
            '• Perpendicular central: perpendicular a la interior en su punto medio.\n'
            '• Perpendicular máxima: la paralela más ancha que cabe en el polígono.\n\n'
            'Nota: el cálculo usa fuerza bruta (O(n²) vértices por polígono).'
            ' En polígonos muy detallados puede ser lento.'
        )

    # ------------------------------------------------------------------
    # Parámetros de entrada y salida
    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr('Capa de polígonos'),
                [QgsProcessing.TypeVectorPolygon]
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CALC_EXT,
                self.tr('Calcular línea exterior más larga'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CALC_PERP,
                self.tr('Calcular perpendicular en punto medio'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.CALC_MAXPERP,
                self.tr('Calcular perpendicular máxima'),
                defaultValue=True
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_INT,
                self.tr('Líneas interiores'),
                type=QgsProcessing.TypeVectorLine
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_EXT,
                self.tr('Líneas exteriores'),
                type=QgsProcessing.TypeVectorLine
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_PERP,
                self.tr('Perpendicular en punto medio'),
                type=QgsProcessing.TypeVectorLine
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_MAXPERP,
                self.tr('Perpendicular máxima'),
                type=QgsProcessing.TypeVectorLine
            )
        )

    # ------------------------------------------------------------------
    # Lógica principal
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

        # --- Lectura de polígonos -------------------------------------------------
        features = list(source.getFeatures())
        total    = len(features)

        dpg = {}  # {fid: shapely Polygon}
        d   = {}  # {fid: [shapely Point, ...]}  — vértices del anillo exterior

        for feat in features:
            if feedback.isCanceled():
                break
            fid  = feat.id()
            geom = wkt_loads(feat.geometry().asWkt())

            # Si es MultiPolygon tomamos la parte con mayor área
            if geom.geom_type == 'MultiPolygon':
                poly = max(geom.geoms, key=lambda p: p.area)
            else:
                poly = geom

            dpg[fid] = poly
            d[fid]   = [Point(c) for c in poly.exterior.coords]

        # --- Búsqueda de líneas más largas (fuerza bruta O(n²)) ------------------
        din  = {}  # {fid: LineString}  línea interior más larga
        dout = {}  # {fid: LineString}  línea exterior más larga

        for idx, (fid, vertices) in enumerate(d.items()):
            if feedback.isCanceled():
                break

            feedback.setProgress(int(idx / total * 50))
            feedback.pushInfo(self.tr(f'Calculando líneas del polígono {fid} '
                                      f'({len(vertices)} vértices)...'))

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

        # --- Escritura de líneas interiores y exteriores -------------------------
        for fid, line in din.items():
            self._write_line(sink_int, fields, fid, line)

        if calc_ext:
            for fid, line in dout.items():
                self._write_line(sink_ext, fields, fid, line)

        # --- Cálculo de perpendiculares ------------------------------------------
        if calc_perp:
            dperp    = {}
            max_perp = {}

            for idx, (fid, v) in enumerate(din.items()):
                if feedback.isCanceled():
                    break

                feedback.setProgress(50 + int(idx / max(len(din), 1) * 50))
                feedback.pushInfo(self.tr(f'Calculando perpendiculares del polígono {fid}...'))

                # Punto medio de la línea interior
                mid = v.interpolate(0.5, normalized=True)

                # Ángulo (bearing) de la línea interior en grados
                pt1, pt2 = list(v.coords)[0], list(v.coords)[1]
                bearing  = math.degrees(math.atan2(pt2[1] - pt1[1], pt2[0] - pt1[0]))

                # Dirección perpendicular: giramos ±90°
                ang1 = math.radians(bearing - 90)
                ang2 = math.radians(bearing + 90)

                # Proyectamos dos puntos a ambos lados del punto medio
                p1 = Point(mid.x + v.length * math.cos(ang1),
                           mid.y + v.length * math.sin(ang1))
                p2 = Point(mid.x + v.length * math.cos(ang2),
                           mid.y + v.length * math.sin(ang2))

                perpline     = LineString((p1, p2))
                intersection = perpline.intersection(dpg[fid])

                if not intersection.is_empty:
                    dperp[fid] = intersection

                # Perpendicular máxima: desplazamos la perpendicular en pasos paralelos
                if calc_maxperp:
                    md   = 0.0
                    step = v.length / 100.0

                    for i in range(-50, 51):
                        try:
                            offset = step * i
                            # parallel_offset: positivo → izquierda, negativo → derecha
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
    # Utilidades
    # ------------------------------------------------------------------
    def _write_line(self, sink, fields, fid, geom):
        """Escribe una geometría shapely como QgsFeature en un sink."""
        if geom is None or geom.is_empty:
            return
        feat = QgsFeature(fields)
        feat.setAttributes([int(fid), float(geom.length)])
        feat.setGeometry(QgsGeometry.fromWkt(geom.wkt))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)
