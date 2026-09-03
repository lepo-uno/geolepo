# -*- coding: utf-8 -*-
"""KUGI Converter - plugin QGIS untuk standardisasi atribut sesuai KUGI.

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 3 of the License, or
(at your option) any later version.
"""


def classFactory(iface):
    from .plugin import KugiStandardizerPlugin
    return KugiStandardizerPlugin(iface)
