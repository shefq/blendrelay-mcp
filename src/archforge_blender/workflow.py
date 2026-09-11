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


def instructions(mode, instance, mesh, visual, only_selected):
    return f'''Operate on the current Blender scene using ArchForge. instance_id={instance}.
Workflow: {mode}. Current scene context supersedes previous assumptions.
{'Work in coherent construction stages for large builds; inspect scene layout and validate each meaningful stage as needed.' if mode == 'BUILD' else 'Keep this request focused on the selected change; expand inspection only to resolve a specific uncertainty.'}
Use supplied selection data first. Inspect named targets once only if needed; inspect
adjacent geometry where necessary to preserve connections. Do not enumerate the whole
scene for a selected edit. Do not search old prompts, logs, addon code or run environment
diagnostics during modeling. Read attached reference images when relevant.
Aim for inspection, edit, verification; this is a soft target, not a hard call limit.
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
