# -*- coding: utf-8 -*-
"""
rmpi_qgis.py — Random Move Points Inside (QGIS Processing Algorithm)

Toma una capa de puntos donde cada punto pertenece a un grupo (campo seleccionable)
y mueve aleatoriamente cada grupo dentro de un extent (dibujable sobre el mapa).

El proceso para cada grupo:
  1. Calcular el centro de gravedad (ceg) y el centroide geométrico (cc) del grupo.
  2. Rotar todos los puntos del grupo un ángulo aleatorio (0-360°) alrededor del ceg.
  3. Calcular un desplazamiento aleatorio que lleve el grupo al interior del extent.
  4. Trasladar los puntos rotados.

Modo de contención (parámetro):
  - Solo ceg: el desplazamiento se calcula para que el ceg quede dentro del extent
              usando cuadrantes aleatorios (comportamiento original, más rápido).
  - Todos los puntos: se calcula el bbox del grupo ya rotado y se deriva directamente
                      el rango válido de desplazamiento, garantizando que ningún punto
                      quede fuera (sin bucle de reintentos).

Instalación:
    Copia este archivo en:
      ~/.local/share/QGIS/QGIS3/profiles/default/processing/scripts/
    y recarga los scripts en QGIS (Processing > Scripts > Recargar scripts).
"""

import random

import numpy as np
from shapely import affinity
from shapely.geometry import Point, box as shapely_box

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


class RandomMovePointsInside(QgsProcessingAlgorithm):

    INPUT_POINTS = 'INPUT_POINTS'
    EXTENT       = 'EXTENT'
    ID_FIELD     = 'ID_FIELD'
    ALL_POINTS   = 'ALL_POINTS'
    OUT_POINTS   = 'OUT_POINTS'
    OUT_CG       = 'OUT_CG'
    OUT_CC       = 'OUT_CC'
    OUT_CG_ORIG  = 'OUT_CG_ORIG'
    OUT_CC_ORIG  = 'OUT_CC_ORIG'

    MAX_TRIES = 100  # solo usado en modo ceg

    # ------------------------------------------------------------------
    # Metadatos del algoritmo
    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return RandomMovePointsInside()

    def name(self):
        return 'randommovepointsinside'

    def displayName(self):
        return self.tr('Mover y rotar grupos de puntos aleatoriamente')

    def group(self):
        return self.tr('Digdgeo')

    def groupId(self):
        return 'digdgeo'

    def shortHelpString(self):
        return self.tr(
            'Mueve y rota grupos de puntos de forma aleatoria dentro de un extent.\n\n'
            'Modos de contención:\n'
            '• Solo ceg (por defecto): garantiza que el centro de gravedad quede dentro '
            'del extent. Rápido, pero algunos puntos del grupo pueden salirse.\n'
            '• Todos los puntos: rota el grupo primero y calcula el desplazamiento '
            'a partir del bounding box rotado, asegurando que ningún punto quede fuera. '
            'Si el grupo es más grande que el extent, no se mueve y se emite un aviso.\n\n'
            'Salidas:\n'
            '• Puntos movidos y rotados\n'
            '• Centros de gravedad originales y desplazados\n'
            '• Centroides geométricos originales y desplazados'
        )

    # ------------------------------------------------------------------
    # Parámetros de entrada y salida
    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT_POINTS,
                self.tr('Capa de puntos'),
                [QgsProcessing.TypeVectorPoint]
            )
        )
        self.addParameter(
            QgsProcessingParameterExtent(
                self.EXTENT,
                self.tr('Extent del área de movimiento')
            )
        )
        self.addParameter(
            QgsProcessingParameterField(
                self.ID_FIELD,
                self.tr('Campo identificador de grupo'),
                parentLayerParameterName=self.INPUT_POINTS,
                defaultValue='ID_progres'
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ALL_POINTS,
                self.tr('Asegurar que todos los puntos queden dentro del extent'),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_POINTS,
                self.tr('Puntos movidos y rotados'),
                type=QgsProcessing.TypeVectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_CG,
                self.tr('Centros de gravedad desplazados'),
                type=QgsProcessing.TypeVectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_CC,
                self.tr('Centroides geométricos desplazados'),
                type=QgsProcessing.TypeVectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_CG_ORIG,
                self.tr('Centros de gravedad originales'),
                type=QgsProcessing.TypeVectorPoint
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_CC_ORIG,
                self.tr('Centroides geométricos originales'),
                type=QgsProcessing.TypeVectorPoint
            )
        )

    # ------------------------------------------------------------------
    # Lógica principal
    # ------------------------------------------------------------------
    def processAlgorithm(self, parameters, context, feedback):
        source_points = self.parameterAsSource(parameters, self.INPUT_POINTS, context)
        id_field      = self.parameterAsString(parameters, self.ID_FIELD, context)
        all_points    = self.parameterAsBoolean(parameters, self.ALL_POINTS, context)

        extent = self.parameterAsExtent(parameters, self.EXTENT, context,
                                        source_points.sourceCrs())
        minx, miny = extent.xMinimum(), extent.yMinimum()
        maxx, maxy = extent.xMaximum(), extent.yMaximum()
        marco_poly  = shapely_box(minx, miny, maxx, maxy)

        modo = self.tr('todos los puntos') if all_points else self.tr('solo ceg')
        feedback.pushInfo(self.tr(
            f'Extent: X [{minx:.1f} – {maxx:.1f}]  Y [{miny:.1f} – {maxy:.1f}] | '
            f'Modo de contención: {modo}'
        ))

        fields_pts = source_points.fields()
        crs        = source_points.sourceCrs()

        fields_cc = QgsFields()
        fields_cc.append(QgsField('id',     QVariant.String))
        fields_cc.append(QgsField('angulo', QVariant.Int))
        fields_cc.append(QgsField('xoff',   QVariant.Double))
        fields_cc.append(QgsField('yoff',   QVariant.Double))

        fields_orig = QgsFields()
        fields_orig.append(QgsField('id', QVariant.String))

        (sink_pts,     dest_pts)     = self.parameterAsSink(parameters, self.OUT_POINTS,  context, fields_pts,  QgsWkbTypes.Point, crs)
        (sink_cg,      dest_cg)      = self.parameterAsSink(parameters, self.OUT_CG,      context, fields_cc,   QgsWkbTypes.Point, crs)
        (sink_cc,      dest_cc)      = self.parameterAsSink(parameters, self.OUT_CC,      context, fields_cc,   QgsWkbTypes.Point, crs)
        (sink_cg_orig, dest_cg_orig) = self.parameterAsSink(parameters, self.OUT_CG_ORIG, context, fields_orig, QgsWkbTypes.Point, crs)
        (sink_cc_orig, dest_cc_orig) = self.parameterAsSink(parameters, self.OUT_CC_ORIG, context, fields_orig, QgsWkbTypes.Point, crs)

        # --- Agrupación de puntos por ID -----------------------------------------
        grupos = {}
        for feat in source_points.getFeatures():
            gid = feat[id_field]
            grupos.setdefault(gid, []).append(feat)

        total = len(grupos)
        feedback.pushInfo(self.tr(f'Se han encontrado {total} grupos de puntos.'))

        # --- Proceso principal: un único paso por grupo ---------------------------
        for idx, (gid, feats) in enumerate(grupos.items()):
            if feedback.isCanceled():
                break

            feedback.setProgress(int(idx / total * 100))
            feedback.pushInfo(self.tr(f'Procesando grupo {gid}...'))

            xs = [f.geometry().asPoint().x() for f in feats]
            ys = [f.geometry().asPoint().y() for f in feats]

            ceg    = (float(np.mean(xs)), float(np.mean(ys)))
            cc     = ((max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0)
            ang    = random.randint(0, 360)

            # Centroides originales
            self._write_orig(sink_cg_orig, fields_orig, gid, Point(ceg))
            self._write_orig(sink_cc_orig, fields_orig, gid, Point(cc))

            if all_points:
                xoff, yoff, rotated = self._displace_all_points(
                    feats, ceg, ang, minx, miny, maxx, maxy, gid, feedback
                )
                for feat, rp in zip(feats, rotated):
                    rpt = affinity.translate(rp, xoff, yoff)
                    self._write_feature(sink_pts, fields_pts, feat, rpt)
            else:
                xoff, yoff = self._displace_ceg(
                    ceg, marco_poly, minx, miny, maxx, maxy, gid, feedback
                )
                for feat in feats:
                    pt  = Point(feat.geometry().asPoint().x(),
                                feat.geometry().asPoint().y())
                    rp  = affinity.rotate(pt, ang, origin=ceg)
                    rpt = affinity.translate(rp, xoff, yoff)
                    self._write_feature(sink_pts, fields_pts, feat, rpt)

            ceg_moved = affinity.translate(Point(ceg), xoff, yoff)
            cc_moved  = affinity.translate(Point(cc),  xoff, yoff)
            self._write_point(sink_cg, fields_cc, gid, ang, xoff, yoff, ceg_moved)
            self._write_point(sink_cc, fields_cc, gid, ang, xoff, yoff, cc_moved)

        return {
            self.OUT_POINTS:  dest_pts,
            self.OUT_CG:      dest_cg,
            self.OUT_CC:      dest_cc,
            self.OUT_CG_ORIG: dest_cg_orig,
            self.OUT_CC_ORIG: dest_cc_orig,
        }

    # ------------------------------------------------------------------
    # Modos de desplazamiento
    # ------------------------------------------------------------------
    def _displace_ceg(self, ceg, marco_poly, minx, miny, maxx, maxy, gid, feedback):
        """
        Desplazamiento aleatorio por cuadrantes: el ceg desplazado debe caer
        dentro del extent. Intenta MAX_TRIES veces; si falla devuelve (0, 0).
        """
        esquinas = {
            'NW': (minx, maxy), 'NE': (maxx, maxy),
            'SW': (minx, miny), 'SE': (maxx, miny),
        }
        for _ in range(self.MAX_TRIES):
            cuadrante = random.choice(list(esquinas.keys()))
            cx, cy    = esquinas[cuadrante]
            dx, dy    = cx - ceg[0], cy - ceg[1]
            xoff = round(random.uniform(min(0, dx), max(0, dx)), 2)
            yoff = round(random.uniform(min(0, dy), max(0, dy)), 2)
            if marco_poly.contains(affinity.translate(Point(ceg), xoff, yoff)):
                feedback.pushInfo(self.tr(
                    f'  Grupo {gid} → {cuadrante} (dx={xoff:.1f}, dy={yoff:.1f})'
                ))
                return xoff, yoff

        feedback.pushWarning(self.tr(
            f'No se encontró desplazamiento válido para el grupo {gid} '
            f'tras {self.MAX_TRIES} intentos. El grupo no se moverá.'
        ))
        return 0.0, 0.0

    def _displace_all_points(self, feats, ceg, ang, minx, miny, maxx, maxy, gid, feedback):
        """
        Rota el grupo primero y deriva el rango válido de desplazamiento a partir
        del bounding box de los puntos rotados. Garantiza que todos los puntos
        queden dentro del extent en un único sorteo (sin reintentos).

        Devuelve (xoff, yoff, lista_de_puntos_rotados).
        Si el grupo es más grande que el extent, devuelve (0, 0, puntos_sin_rotar).
        """
        rotated = [
            affinity.rotate(
                Point(f.geometry().asPoint().x(), f.geometry().asPoint().y()),
                ang, origin=ceg
            )
            for f in feats
        ]

        rxs = [p.x for p in rotated]
        rys = [p.y for p in rotated]

        # Rango de xoff/yoff que mantiene todos los puntos dentro del extent
        xoff_min = minx - min(rxs)
        xoff_max = maxx - max(rxs)
        yoff_min = miny - min(rys)
        yoff_max = maxy - max(rys)

        if xoff_min > xoff_max or yoff_min > yoff_max:
            feedback.pushWarning(self.tr(
                f'El grupo {gid} es más grande que el extent tras la rotación. '
                f'El grupo no se moverá.'
            ))
            return 0.0, 0.0, rotated

        xoff = round(random.uniform(xoff_min, xoff_max), 2)
        yoff = round(random.uniform(yoff_min, yoff_max), 2)
        feedback.pushInfo(self.tr(
            f'  Grupo {gid}: ang={ang}° dx={xoff:.1f} dy={yoff:.1f}'
        ))
        return xoff, yoff, rotated

    # ------------------------------------------------------------------
    # Utilidades de escritura
    # ------------------------------------------------------------------
    def _write_feature(self, sink, fields, feat, point):
        out = QgsFeature(fields)
        out.setAttributes(feat.attributes())
        out.setGeometry(QgsGeometry.fromWkt(point.wkt))
        sink.addFeature(out, QgsFeatureSink.FastInsert)

    def _write_point(self, sink, fields, gid, angulo, xoff, yoff, point):
        feat = QgsFeature(fields)
        feat.setAttributes([str(gid), int(angulo), float(xoff), float(yoff)])
        feat.setGeometry(QgsGeometry.fromWkt(point.wkt))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)

    def _write_orig(self, sink, fields, gid, point):
        feat = QgsFeature(fields)
        feat.setAttributes([str(gid)])
        feat.setGeometry(QgsGeometry.fromWkt(point.wkt))
        sink.addFeature(feat, QgsFeatureSink.FastInsert)
