# Grass Screenshot Approximate Map Inputs

These files were manually/visually derived from the uploaded Grass roguelike screenshot.

- map.txt: approximate 26 x 20 material-layer ASCII map
- legend.json: symbol-to-material legend
- style.txt: global visual style
- objects_optional.json: approximate props/actors, separated from material generation
- material_generation_subset.json: recommended first subset for testing material generation

Important:
- This is an approximate reconstruction, not pixel-perfect OCR.
- Symbol `0` is void/empty and should be skipped by the material-generation pipeline.
- Use legend entries with `generate_material: true` for material slots.
- For a first pipeline test, generate only: stone_wall, stone_floor, wood_planks, wooden_door, grass_ground, water.
