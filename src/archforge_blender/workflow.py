"""Quality-first task profiles and compact scene context."""
import re


RESOURCE_PROFILES = {
    'OBJECT_MATERIAL': dict(initial_calls=15, initial_edits=4, polls_per_job=10,
                            max_calls=60, max_edits=16, verification_passes=1),
    'FULL_BUILD': dict(initial_calls=40, initial_edits=10, polls_per_job=20,
                       max_calls=120, max_edits=30, verification_passes=2),
    'BUILD_ASSETS': dict(initial_calls=60, initial_edits=15, polls_per_job=30,
                         max_calls=180, max_edits=45, verification_passes=2),
    'COMPLEX_SCENE': dict(initial_calls=100, initial_edits=25, polls_per_job=40,
                          max_calls=250, max_edits=60, verification_passes=3),
}

RESOURCE_MODE_ITEMS = (
    ('OBJECT_MATERIAL', 'Object / Material', 'Localized object, material, lighting, or modifier work'),
    ('FULL_BUILD', 'Full Build', 'Complete procedural model such as a house, room, product, or vehicle'),
    ('BUILD_ASSETS', 'Build + Assets', 'Complete build with asset discovery, imports, and visual refinement'),
    ('COMPLEX_SCENE', 'Complex Scene', 'Large detailed environments, animation, simulation, or multi-stage work'),
)

QUALITY_ITEMS = (
    ('DRAFT', 'Draft', 'Fast blockout with basic materials and one verification view'),
    ('STANDARD', 'Standard', 'Complete usable scene with appropriate detail'),
    ('HIGH', 'High', 'Detailed scene with materials, lighting, props, and correction passes'),
    ('MAXIMUM', 'Maximum', 'Highest detail with multiple visual checks and refinement passes'),
)

PERMISSION_ITEMS = (
    ('SAFE', 'Safe', 'Use the individual Python and provider permission switches'),
    ('AUTONOMOUS', 'Autonomous', 'Authorize unattended ArchForge editing and asset operations for this scene'),
)

QUALITY_SCALE = {'DRAFT': .6, 'STANDARD': .85, 'HIGH': 1.0, 'MAXIMUM': 1.35}


def profile(mode, quality='HIGH'):
    result = dict(RESOURCE_PROFILES.get(mode, RESOURCE_PROFILES['FULL_BUILD']))
    scale = QUALITY_SCALE.get(quality, 1.0)
    for key in ('initial_calls', 'initial_edits'):
        result[key] = max(1, round(result[key] * scale))
    if quality == 'DRAFT': result['verification_passes'] = 1
    if quality == 'MAXIMUM': result['verification_passes'] += 1
    result.update(mode=mode, quality=quality, auto_extend=True)
    return result


def resource_limits(mode, quality='HIGH'):
    current = profile(mode, quality)
    return current['initial_calls'], current['initial_edits'], current['polls_per_job']


def choose(requested, prompt, selected):
    if requested != 'AUTO': return requested
    build = re.search(r'\b(entire|whole scene|complete|build|generate|create|city|environment|house|room|terrain)\b',prompt,re.I)
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


def instructions(mode, instance, mesh, visual, only_selected, max_mcp_calls=40,
                 max_edit_attempts=10, max_job_polls=20, resource_mode='FULL_BUILD',
                 quality='HIGH', verification_passes=2, autonomous=False):
    build = mode == 'BUILD'
    if resource_mode == 'COMPLEX_SCENE':
        strategy = '''Treat this as a complex production scene. Work through scene brief and composition,
blockout, primary geometry, secondary detail, materials, environment/assets, lighting/camera,
multi-view visual review, then targeted correction. Finish the background as well as focal objects.'''
    elif resource_mode == 'BUILD_ASSETS':
        strategy = '''Build the complete scene in coherent stages. Use permitted cached or provider assets when
they improve realism, place and scale them correctly, then finish lighting, camera, review and correction.'''
    elif build:
        strategy = '''Build the complete result in coherent stages: composition, primary geometry,
detail, materials, lighting/camera, visual review and correction.'''
    else:
        strategy = 'Make the selected change precisely, inspect adjacent geometry only when needed, then verify it.'
    quality_rule = {
        'DRAFT': 'Aim for a clear blockout and readable basic materials.',
        'STANDARD': 'Deliver a complete usable scene with consistent scale and materials.',
        'HIGH': 'Deliver polished geometry, material variation, lighting, composition and secondary detail.',
        'MAXIMUM': 'Pursue the strongest result with rich detail, believable variation, complete surroundings, polished lighting and repeated visual refinement.',
    }.get(quality, '')
    python_rule = ('For full scene construction, execute_blender_python is the primary creation tool; use it freely '
                   'in coherent stages. Typed tools support inspection, selected mesh work, assets and verification.'
                   if build else 'Prefer mesh_edit for supported selected-mesh changes; use execute_blender_python for more expressive work.')
    permission_rule = ('The user selected Autonomous generation and authorized ArchForge editing and asset operations for this scene.'
                       if autonomous else 'Respect the active scene permission switches.')
    visual_rule = (f'Capture and inspect at least {verification_passes} useful viewport view group(s) during and after construction. '
                   'Use capture_viewport for fast general checks. Use capture_focused_view for localized edits, joints, façades, '
                   'and reference matching (store camera_matrix_world before editing to replay for exact before/after comparison). '
                   'Check composition, scale, missing geometry, materials, lighting, intersections, floating objects and unfinished background. '
                   'Apply concrete corrections and recapture a final view.' if visual else
                   'Use geometry and scene-state checks because visual verification is disabled.')
    return f'''Operate on the current Blender scene through ArchForge. instance_id={instance}.
Workflow={mode}; workload={resource_mode}; output_quality={quality}.
Current scene state and the current user request supersede old conversation assumptions.

RESULT STRATEGY:
{strategy}
{quality_rule}
Before editing, derive a compact internal scene brief covering scale, composition, primary and secondary
elements, materials, lighting, camera, exclusions and visible quality targets. Do not spend a tool call reporting it.
{python_rule}
{permission_rule}

Use supplied selection and reference images directly. Do not inspect an empty selection or repeatedly inspect
an empty scene. Do not search logs, wrapper files, add-on source or the run environment while modeling.
For assets, read policy once, search cache/providers, import useful permitted results, and continue procedurally
when none fits. Asset work and Blender construction may be interleaved.

The starting allowance is {max_mcp_calls} MCP calls and {max_edit_attempts} accepted edits. It expands
automatically while operations succeed, so do not lower quality to conserve calls. Each operation may be checked
up to {max_job_polls} times while queued or running. Never poll terminal work or resubmit uncertain work with a
new ID. A rejected or transport-failed submission does not consume an edit.

Return short structured results from Python rather than source or geometry dumps. Keep successful partial work
if a later helper fails and make a targeted correction. Treat MCP isError, executed:false and failed status as
failure and correct them. {'Target selected elements with live bmesh, preserve Edit Mode and validate topology.' if mesh else 'Preserve useful viewport interaction and selection state.'}
{'Only the selected target and necessary connected geometry may change.' if only_selected else 'Organize generated content into clearly named collections and objects.'}

VISUAL COMPLETION:
{visual_rule}
For builds, do not finish before primary geometry, materials, lighting/world, camera, requested exclusions and
the full visible environment are addressed. Report completed stages and real remaining limitations concisely.
'''
