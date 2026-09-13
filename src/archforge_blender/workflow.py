"""Task-sized instructions and compact scene payloads; these are not a sandbox."""
import re


def choose(requested, prompt, selected):
    if requested != 'AUTO': return requested
    build = re.search(r'\b(entire|whole scene|complete house|full house|build a house|generate a house|two.storey|two.story)\b',prompt,re.I)
    return 'EDIT' if selected and not build else 'BUILD'


def compact(value):
    if isinstance(value,float): return round(value,6)
    if isinstance(value,list): return [compact(v) for v in value]
    if isinstance(value,dict):
        result={k:compact(v) for k,v in value.items() if k not in {'world','normal_world'}}
        if 'vertices' in result and 'adjacent_vertices' in result:
            indices={v['index'] for v in result['vertices'] if isinstance(v,dict) and 'index' in v}
            result['adjacent_vertices']=[v for v in result['adjacent_vertices'] if v['index'] not in indices]
        return result
    return value


def instructions(mode, instance, mesh, visual, only_selected, max_mcp_calls=6, max_edit_attempts=1, max_job_polls=1):
    return f'''Operate on the current Blender scene using ArchForge. instance_id={instance}.
Workflow: {mode}. Current scene context supersedes previous assumptions.
{'Work in coherent construction stages for large builds; inspect scene layout and validate each meaningful stage as needed.' if mode == 'BUILD' else 'Keep this request focused on the selected change; expand inspection only to resolve a specific uncertainty.'}
Use supplied selection data first. Inspect named targets once only if needed; inspect
adjacent geometry where necessary to preserve connections. Do not enumerate the whole
scene for a selected edit. Do not search old prompts, logs, addon code or run environment
diagnostics during modeling. Read attached reference images when relevant.
For external assets use get_asset_policy, search_assets and import_asset only; never
change provider settings or download assets through Python, shell or other providers.
Search cache first. Prefer Poly Haven for realistic materials/HDRIs/environment and
Poly Pizza for lightweight props. Respect licence and size limits; use procedural
geometry if no permitted asset fits. Poll asset_job only while queued/running.
MCP CALL BUDGET: use no more than {max_mcp_calls} total ArchForge MCP tool calls for
this request, including job-status polls and asset operations. Plan before calling tools.
EDIT ATTEMPTS: perform no more than {max_edit_attempts} edit attempt(s). Make one
coherent operation per attempt; do not split a simple change into many micro-edits.
JOB POLLS: use no more than {max_job_polls} status poll(s) for an operation, and only
when its immediately returned status is queued or running. A completed response needs
no poll. If a budget would be exceeded, stop and report the remaining uncertainty.
Use archforge_blender_command with action and arguments as an object. It waits briefly;
when status is complete, consume the result and continue. Poll only queued/running jobs.
Never keep polling a terminal job or resubmit a timed-out edit with a new ID.
Prefer mesh_edit for supported selected-mesh operations: bevel, extrude, inset, bridge,
move_normal, assign_material. distance is in local mesh units; account for object scale.
Use execute with code for complex geometry; return a short result rather than dumping
geometry or source. Preserve unrelated content and overlays. Never restore a scene to
recover from a script error; inspect the partial result and make a targeted correction.
{'Target selected mesh elements, not whole objects. Use live bmesh.from_edit_mesh; never free it. Update with bmesh.update_edit_mesh. Preserve Edit Mode and resulting selection. Indices can change after topology edits; verify live selection, particularly when context is truncated.' if mesh else 'Preserve the starting interaction mode and selection where possible.'}
{'Only selected geometry may change; read adjacent geometry for connectivity.' if only_selected else 'Use the selection as the intended target unless the request specifies otherwise.'}
For topology changes, compare validate_selection before/after (mesh_edit already returns
both). Fix newly introduced degenerate geometry; open boundaries may be intentional.
{'For shape, appearance, alignment or reference matching, take one screenshot with max_dimension=512 after editing. Request more detail/views only if this leaves a specific uncertainty.' if visual else 'Visual checking is disabled by the user; use relevant geometry checks.'}
Allow a targeted correction when verification finds a concrete defect. Stop repeating
an unchanged inspection: explain what is missing if a necessary result remains unavailable.
Report what changed and any verification limits concisely.
'''
