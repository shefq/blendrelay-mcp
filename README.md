# ArchForge MCP

ArchForge MCP connects a local MCP client and Blender 4.5 so an agent can
inspect and edit any Blender scene. It includes a procedural architecture
workflow, full-scene checkpoints, viewport sketches, and a policy-controlled
Asset Library.

## Setup

Install the Python package from this repository, then install or link the
`src/archforge_blender` extension in Blender. Set the add-on Runtime setting to
the same data directory used by the runtime. By default it is:

`%LOCALAPPDATA%\ArchForgeMCP`

Start the runtime with:

```powershell
archforge-runtime serve
```

Configure the MCP gateway to run `archforge-mcp`. In Blender, open **3D View →
ArchForge**, then click **Connect / Refresh**.

## Asset Library

The Asset Library is a separate panel in the ArchForge sidebar. Provider policy
is stored in the current Blender scene, so opening an older scene receives safe
defaults:

- Local Asset Cache is always available.
- Poly Haven is enabled by default. Its public catalogue assets are CC0.
- Poly Pizza is enabled by default; CC0 is enabled and CC-BY is disabled.
- Maximum download size is 200 MB and can be changed from 1 to 2048 MB.

Search checks the local cache first. When no suitable cached asset exists, it
searches only enabled providers and returns only assets allowed by the licence
filters. Poly Haven is suitable for HDRIs, PBR materials, rocks, terrain, and
realistic environment assets. Poly Pizza is suitable for lightweight props,
low-poly background assets, and rapid scene population.

Poly Pizza CC-BY assets require explicit opt-in. Each CC-BY import retains the
creator, source URL, and licence metadata on the imported collection and data
blocks, and adds a non-destructive record to `ATTRIBUTION.txt`. If CC-BY is
disabled the library reports: “This asset requires CC-BY attribution, which is
disabled in Asset Provider Policy.”

The public Poly Haven API does not require an API key, so ArchForge has no API
key field or account setup. Asset downloads are made by the local runtime, not
by Blender itself.

Downloads are stored at:

`%LOCALAPPDATA%\ArchForgeMCP\assets\<provider>\<asset-id>`

Each directory contains the original cached files and `metadata.json` with the
provider, asset ID, licence, creator, source, date, tags, original filename,
and integrity hashes. `ATTRIBUTION.txt` is stored directly in the `assets`
directory. Cached files are verified before import and are reused instead of
being downloaded again.

Imports create `ArchForge Assets / <Provider> / <Asset Name>` collections.
They leave unrelated objects, cameras, collections, materials, and active world
lighting alone. HDRIs are imported as separate World assets rather than being
made active automatically.

GLB and GLTF import uses Blender's built-in glTF importer. If Blender reports
that its bundled NumPy installation is incomplete, repair or reinstall Blender;
ArchForge stops the import before changing the scene.

## MCP asset tools

The gateway exposes these policy-enforced tools:

- `get_asset_policy`
- `search_assets`
- `import_asset`
- `list_cached_assets`
- `refresh_asset_cache`
- `asset_job` for asynchronous search and import results

Agents must use these tools for external assets. If no asset is permitted or
both providers are disabled, the intended fallback is procedural Blender
geometry.

## Development checks

Run the standard test suite from the repository root:

```powershell
python -B -m unittest discover -s tests -p "test_*.py" -v
```
