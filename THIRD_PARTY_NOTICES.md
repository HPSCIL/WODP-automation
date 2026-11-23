# Third-party runtime notices

The proprietary wind engine is distributed as a compiled executable. Its scientific implementation is not included.

Third-party components retain their own licenses. Notices for nlohmann/json, NumPy/PCG seed compatibility,
Rasterio, GDAL and PROJ are included under bin/licenses, bin/gdal_data/LICENSE.TXT and bin/proj_data/copyright.
The native GIS runtime is taken from the local Rasterio 1.5.0 Windows wheel, with its associated data files.
It includes dynamically linked GDAL/PROJ/GEOS and their supporting libraries. Version/component metadata, where
provided by the runtime, is retained in the data folders. Microsoft app-local CRT binaries accompany the GIS runtime.

Upstream projects:

- GDAL: https://gdal.org/en/stable/license.html
- PROJ: https://proj.org/en/stable/about.html
- GEOS: https://libgeos.org/usage/license/
- NumPy: https://github.com/numpy/numpy/tree/v2.4.2
- PCG: https://www.pcg-random.org/
- nlohmann/json: https://github.com/nlohmann/json/tree/v3.11.3
- Rasterio: https://github.com/rasterio/rasterio

This local package does not assert exclusive ownership over those dependencies.
