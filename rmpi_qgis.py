# -*- coding: utf-8 -*-
"""
rmpi_qgis.py — Random Move Geometries Inside (QGIS Processing Algorithm)

Toma una capa vectorial (puntos, líneas o polígonos) donde cada feature pertenece
a un grupo (campo seleccionable) y mueve aleatoriamente cada grupo dentro de un
extent definido por el usuario.

El proceso para cada grupo:
  1. Calcular el centro de gravedad (ceg) del grupo a partir de los centroides de
     sus features, y el centroide geométrico (cc) como centro del bounding box.
  2. Rotar todas las geometrías del grupo un ángulo aleatorio (0-360°) alrededor del ceg.
  3. Calcular un desplazamiento aleatorio que lleve el grupo al interior del extent.
  4. Trasladar las geometrías rotadas.

Modos de contención:
  - Solo ceg (por defecto): garantiza que el ceg quede dentro del extent.
  - Todas las geometrías: calcula el bbox de la unión de las geometrías rotadas y
    deriva directamente el rango válido de desplazamiento. Si el grupo es más grande
    que el extent, no se mueve y se emite un aviso.

Instalación:
    Copia este archivo en:
      ~/.local/share/QGIS/QGIS3/profiles/default/processing/scripts/
    y recarga los scripts en QGIS (Processing > Scripts > Recargar scripts).
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
    ID_FIELD    = 'ID_FIELD'
    ALL_GEOMS   = 'ALL_GEOMS'
    OUT_FEATS   = 'OUT_FEATS'
    OUT_CG      = 'OUT_CG'
    OUT_CC      = 'OUT_CC'
    OUT_CG_ORIG = 'OUT_CG_ORIG'
    OUT_CC_ORIG = 'OUT_CC_ORIG'

    MAX_TRIES = 100  # solo usado en modo ceg

    # ------------------------------------------------------------------
    # Metadatos del algoritmo
    # ------------------------------------------------------------------
    def tr(self, string):
        return QCoreApplication.translate('Processing', string)

    def createInstance(self):
        return RandomMoveGeometriesInside()

    def name(self):
        return 'randommovegeometriesinside'

    def displayName(self):
        return self.tr('Mover y rotar grupos de geometrías aleatoriamente')

    def group(self):
        return self.tr('Digdgeo')

    def groupId(self):
        return 'digdgeo'

    def shortHelpString(self):
        return self.tr(
            'Mueve y rota grupos de geometrías (puntos, líneas o polígonos) de forma '
            'aleatoria dentro de un extent.\n\n'
            'El tipo de geometría se detecta automáticamente. Para líneas y polígonos, '
            'el centro de gravedad del grupo se calcula como la media de los centroides '
            'de sus features.\n\n'
            'Modos de contención:\n'
            '• Solo ceg (por defecto): garantiza que el centro de gravedad quede dentro '
            'del extent. Algunas geometrías pueden salirse.\n'
            '• Todas las geometrías: calcula el bbox de la unión rotada del grupo y '
            'deriva el rango válido de desplazamiento en un único sorteo. Si el grupo '
            'es más grande que el extent, no se mueve y se emite un aviso.\n\n'
            'Salidas:\n'
            '• Geometrías movidas y rotadas (atributos originales preservados)\n'
            '• Centros de gravedad originales y desplazados\n'
            '• Centroides geométricos originales y desplazados'
        )

    # ------------------------------------------------------------------
    # Parámetros de entrada y salida
    # ------------------------------------------------------------------
    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT,
                self.tr('Capa vectorial (puntos, líneas o polígonos)'),
                [QgsProcessing.TypeVectorAnyGeometry]
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
                parentLayerParameterName=self.INPUT,
                defaultValue='ID_progres'
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.ALL_GEOMS,
                self.tr('Asegurar que todas las geometrías queden dentro del extent'),
                defaultValue=False
            )
        )
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                self.OUT_FEATS,
                self.tr('Geometrías movidas y rotadas'),
                type=QgsProcessing.TypeVectorAnyGeometry
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
        source    = self.parameterAsSource(parameters, self.INPUT, context)
        id_field  = self.parameterAsString(parameters, self.ID_FIELD, context)
        all_geoms = self.parameterAsBoolean(parameters, self.ALL_GEOMS, context)

        # Tipo de geometría
        wkb_type  = source.wkbType()
        geom_type = QgsWkbTypes.geometryType(wkb_type)
        type_label = {
            QgsWkbTypes.PointGeometry:   'Punto',
            QgsWkbTypes.LineGeometry:    'Línea',
            QgsWkbTypes.PolygonGeometry: 'Polígono',
        }.get(geom_type, 'Desconocido')

        modo = self.tr('todas las geometrías') if all_geoms else self.tr('solo ceg')
        feedback.pushInfo(self.tr(
            f'Tipo de geometría: {type_label} | Modo de contención: {modo}'
        ))

        # Extent
        extent = self.parameterAsExtent(parameters, self.EXTENT, context,
                                        source.sourceCrs())
        minx, miny = extent.xMinimum(), extent.yMinimum()
        maxx, maxy = extent.xMaximum(), extent.yMaximum()
        marco_poly  = shapely_box(minx, miny, maxx, maxy)

        feedback.pushInfo(self.tr(
            f'Extent: X [{minx:.1f} – {maxx:.1f}]  Y [{miny:.1f} – {maxy:.1f}]'
        ))

        # Esquemas de campos
        fields_in   = source.fields()
        crs         = source.sourceCrs()

        fields_cc = QgsFields()
        fields_cc.append(QgsField('id',     QVariant.String))
        fields_cc.append(QgsField('angulo', QVariant.Int))
        fields_cc.append(QgsField('xoff',   QVariant.Double))
        fields_cc.append(QgsField('yoff',   QVariant.Double))

        fields_orig = QgsFields()
        fields_orig.append(QgsField('id', QVariant.String))

        (sink_feats,   dest_feats)   = self.parameterAsSink(parameters, self.OUT_FEATS,   context, fields_in,   wkb_type,          crs)
        (sink_cg,      dest_cg)      = self.parameterAsSink(parameters, self.OUT_CG,      context, fields_cc,   QgsWkbTypes.Point, crs)
        (sink_cc,      dest_cc)      = self.parameterAsSink(parameters, self.OUT_CC,      context, fields_cc,   QgsWkbTypes.Point, crs)
        (sink_cg_orig, dest_cg_orig) = self.parameterAsSink(parameters, self.OUT_CG_ORIG, context, fields_orig, QgsWkbTypes.Point, crs)
        (sink_cc_orig, dest_cc_orig) = self.parameterAsSink(parameters, self.OUT_CC_ORIG, context, fields_orig, QgsWkbTypes.Point, crs)

        # Agrupación de features por ID
        grupos = {}
        for feat in source.getFeatures():
            gid = feat[id_field]
            grupos.setdefault(gid, []).append(feat)

        total = len(grupos)
        feedback.pushInfo(self.tr(f'Se han encontrado {total} grupos.'))

        # Proceso principal
        for idx, (gid, feats) in enumerate(grupos.items()):
            if feedback.isCanceled():
                break

            feedback.setProgress(int(idx / total * 100))
            feedback.pushInfo(self.tr(f'Procesando grupo {gid}...'))

            # Centroides de cada feature (funciona para puntos, líneas y polígonos)
            centroids = [self._feature_centroid(f, geom_type) for f in feats]
            xs = [c[0] for c in centroids]
            ys = [c[1] for c in centroids]

            ceg = (float(np.mean(xs)), float(np.mean(ys)))
            cc  = ((max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0)
            ang = random.randint(0, 360)

            # Centroides originales
            self._write_orig(sink_cg_orig, fields_orig, gid, Point(ceg))
            self._write_orig(sink_cc_orig, fields_orig, gid, Point(cc))

            # Convertir a shapely y rotar alrededor del ceg
            shapely_geoms = [wkt_loads(f.geometry().asWkt()) for f in feats]
            rotated       = [affinity.rotate(g, ang, origin=ceg) for g in shapely_geoms]

            # Calcular desplazamiento
            if all_geoms:
                xoff, yoff = self._displace_all_geoms(
                    rotated, minx, miny, maxx, maxy, gid, feedback
                )
            else:
                xoff, yoff = self._displace_ceg(
                    ceg, marco_poly, minx, miny, maxx, maxy, gid, feedback
                )

            # Trasladar y escribir features
            for feat, rg in zip(feats, rotated):
                rgt = affinity.translate(rg, xoff, yoff)
                out = QgsFeature(fields_in)
                out.setAttributes(feat.attributes())
                out.setGeometry(QgsGeometry.fromWkt(rgt.wkt))
                sink_feats.addFeature(out, QgsFeatureSink.FastInsert)

            # Centroides desplazados
            ceg_moved = affinity.translate(Point(ceg), xoff, yoff)
            cc_moved  = affinity.translate(Point(cc),  xoff, yoff)
            self._write_point(sink_cg, fields_cc, gid, ang, xoff, yoff, ceg_moved)
            self._write_point(sink_cc, fields_cc, gid, ang, xoff, yoff, cc_moved)

        return {
            self.OUT_FEATS:   dest_feats,
            self.OUT_CG:      dest_cg,
            self.OUT_CC:      dest_cc,
            self.OUT_CG_ORIG: dest_cg_orig,
            self.OUT_CC_ORIG: dest_cc_orig,
        }

    # ------------------------------------------------------------------
    # Modos de desplazamiento
    # ------------------------------------------------------------------
    def _displace_ceg(self, ceg, marco_poly, minx, miny, maxx, maxy, gid, feedback):
        """Desplazamiento aleatorio por cuadrantes: solo el ceg debe quedar dentro."""
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

    def _displace_all_geoms(self, rotated, minx, miny, maxx, maxy, gid, feedback):
        """
        Deriva el rango válido de desplazamiento del bbox de la unión de todas
        las geometrías ya rotadas. Un único sorteo, sin reintentos.
        """
        all_bounds = [g.bounds for g in rotated]
        rx_min = min(b[0] for b in all_bounds)
        ry_min = min(b[1] for b in all_bounds)
        rx_max = max(b[2] for b in all_bounds)
        ry_max = max(b[3] for b in all_bounds)

        xoff_min, xoff_max = minx - rx_min, maxx - rx_max
        yoff_min, yoff_max = miny - ry_min, maxy - ry_max

        if xoff_min > xoff_max or yoff_min > yoff_max:
            feedback.pushWarning(self.tr(
                f'El grupo {gid} es más grande que el extent tras la rotación. '
                f'El grupo no se moverá.'
            ))
            return 0.0, 0.0

        xoff = round(random.uniform(xoff_min, xoff_max), 2)
        yoff = round(random.uniform(yoff_min, yoff_max), 2)
        feedback.pushInfo(self.tr(f'  Grupo {gid}: dx={xoff:.1f} dy={yoff:.1f}'))
        return xoff, yoff

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _feature_centroid(self, feat, geom_type):
        """Devuelve (x, y) del centroide de la feature, sea del tipo que sea."""
        if geom_type == QgsWkbTypes.PointGeometry:
            pt = feat.geometry().asPoint()
        else:
            pt = feat.geometry().centroid().asPoint()
        return (pt.x(), pt.y())

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
