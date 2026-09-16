"""Task-aware quality profiles and compact prompt context for general Blender work."""
import re

RESOURCE_PROFILES = {
    "OBJECT_MATERIAL": dict(initial_calls=15, initial_edits=4, polls_per_job=10, max_calls=60, max_edits=16, verification_passes=1),
    "FULL_BUILD": dict(initial_calls=40, initial_edits=10, polls_per_job=20, max_calls=120, max_edits=30, verification_passes=2),
    "BUILD_ASSETS": dict(initial_calls=60, initial_edits=15, polls_per_job=30, max_calls=180, max_edits=45, verification_passes=2),
    "COMPLEX_SCENE": dict(initial_calls=100, initial_edits=25, polls_per_job=40, max_calls=250, max_edits=60, verification_passes=3),
}
RESOURCE_MODE_ITEMS = (
    ("OBJECT_MATERIAL", "Focused Task", "Localized modeling, data, shading, lighting, or modifier work"),
    ("FULL_BUILD", "Full Creation", "Complete objects, scenes, node systems, rigs, or animation work"),
    ("BUILD_ASSETS", "Creation + Assets", "Complete work with asset discovery, imports, and refinement"),
    ("COMPLEX_SCENE", "Complex Production", "Large, detailed, animated, simulated, or multi-stage work"),
)
QUALITY_ITEMS = (
    ("DRAFT", "Draft", "Fast functional result with one relevant check"),
    ("STANDARD", "Standard", "Complete usable result with appropriate detail"),
    ("HIGH", "High", "Polished result with targeted correction passes"),
    ("MAXIMUM", "Maximum", "Highest detail and repeated task-specific verification"),
)
PERMISSION_ITEMS = (("SAFE", "Safe", "Use individual Python and provider permission switches"),
                    ("AUTONOMOUS", "Autonomous", "Authorize unattended Blender editing and asset operations for this scene"))
QUALITY_SCALE = {"DRAFT": .6, "STANDARD": .85, "HIGH": 1.0, "MAXIMUM": 1.35}

CATEGORIES = {
    "ANIMATION": r"\b(animate|animation|keyframe|f-?curve|nla|timeline|motion|walk cycle|lip.?sync)\b",
    "RIGGING": r"\b(rig|armature|bone|skin|weight paint|constraint|ik|fk|pose)\b",
    "SIMULATION": r"\b(simulat|cloth|fluid|smoke|fire|rigid body|soft body|collision|particle|hair|cache)\b",
    "NODES": r"\b(geometry nodes?|shader nodes?|node group|procedural nodes?)\b",
    "COMPOSITING": r"\b(composit|video sequence|vse|color grade|tracking|masking)\b",
    "RENDERING": r"\b(render|lighting|camera|world|cycles|eevee|view layer|output settings?)\b",
    "SHADING": r"\b(material|shader|texture|uv|unwrap|lookdev)\b",
    "DATA": r"\b(rename|organize|collection|clean.?up|purge|link|append|export|import|convert|metadata)\b",
    "SCULPT": r"\b(sculpt|remesh|voxel|multires|retopolog)\b",
    "CREATE": r"\b(create|build|generate|model|make|add|entire|complete|whole scene|environment)\b",
}

def profile(mode, quality="HIGH"):
    result = dict(RESOURCE_PROFILES.get(mode, RESOURCE_PROFILES["FULL_BUILD"]))
    scale = QUALITY_SCALE.get(quality, 1.0)
    for key in ("initial_calls", "initial_edits"): result[key] = max(1, round(result[key] * scale))
    if quality == "DRAFT": result["verification_passes"] = 1
    if quality == "MAXIMUM": result["verification_passes"] += 1
    result.update(mode=mode, quality=quality, auto_extend=True)
    return result

def resource_limits(mode, quality="HIGH"):
    current = profile(mode, quality)
    return current["initial_calls"], current["initial_edits"], current["polls_per_job"]

def choose(requested, prompt, selected=False, blender_mode="OBJECT"):
    if requested != "AUTO": return requested
    for category, pattern in CATEGORIES.items():
        if re.search(pattern, prompt, re.I): return category
    if blender_mode != "OBJECT" or selected: return "EDIT"
    return "CREATE"

def is_creation(category):
    return category in {"BUILD", "CREATE"}

def compact(value):
    if isinstance(value, float): return round(value, 6)
    if isinstance(value, list): return [compact(item) for item in value]
    if isinstance(value, dict):
        result = {key: compact(item) for key, item in value.items() if key not in {"world", "normal_world"}}
        if "vertices" in result and "adjacent_vertices" in result:
            indices = {item["index"] for item in result["vertices"] if isinstance(item, dict) and "index" in item}
            result["adjacent_vertices"] = [item for item in result["adjacent_vertices"] if item["index"] not in indices]
        return result
    return value

TASK_RULES = {
    "ANIMATION": "Inspect actions, F-curves, NLA, constraints and timing. Preserve existing motion unless the request changes it. Verify key frames and representative timeline frames.",
    "RIGGING": "Inspect armatures, hierarchy, constraints, vertex groups and deformation. Verify rest/pose state and representative poses.",
    "SIMULATION": "Inspect scene scale, dependencies, caches and frame range. Configure the requested simulation and verify representative frames without assuming a finished render is required.",
    "NODES": "Inspect relevant node trees, interfaces and evaluated output. Build readable named node groups and verify parameters plus evaluated geometry or shading.",
    "COMPOSITING": "Inspect compositor or sequencer data, inputs, frame range and output settings. Verify the actual processed result at representative frames.",
    "RENDERING": "Inspect cameras, lights, world, color management, engine and output settings. Change only the requested render pipeline and verify with appropriate renders.",
    "SHADING": "Inspect materials, node trees, UVs and image dependencies. Preserve geometry unless the request requires a geometry correction and verify representative surfaces.",
    "DATA": "Inspect datablocks, links, collections and naming before changing them. Verify structure and references; visual rendering is optional.",
    "SCULPT": "Inspect scale, topology, modifiers and sculpt state. Preserve useful detail and verify silhouette, topology and requested surface changes.",
    "EDIT": "Make the selected or local change precisely, inspect connected dependencies when needed, and use mode-appropriate verification.",
    "BUILD": "Create the complete requested Blender result in coherent stages and verify every requested component.",
    "CREATE": "Create the complete requested Blender result in coherent stages and verify every requested component.",
}

def instructions(mode, instance, edit_mode, visual, only_selected, max_mcp_calls=40,
                 max_edit_attempts=10, max_job_polls=20, resource_mode="FULL_BUILD",
                 quality="HIGH", verification_passes=2, autonomous=False):
    task_rule = TASK_RULES.get(mode, TASK_RULES["EDIT"])
    quality_rule = {"DRAFT": "Deliver a functional draft.", "STANDARD": "Deliver a complete usable result.",
                    "HIGH": "Deliver a polished result with appropriate detail.",
                    "MAXIMUM": "Pursue the strongest result with repeated task-specific refinement."}.get(quality, "")
    visual_rule = (f"Capture and inspect at least {verification_passes} useful view group(s) when images can prove the result. "
                   "Use structural checks for data, rigging, animation, nodes and settings; use representative frames for time-dependent work. "
                   "Apply corrections and repeat only the checks that matter." if visual else
                   "Use task-specific structural and scene-state checks because visual verification is disabled.")
    mode_rule = (f"The prompt began in Blender mode {edit_mode}. Preserve or restore that mode where possible and operate on its current selection using Blender's native data APIs."
                 if edit_mode else "Preserve useful viewport interaction and selection state.")
    permission = ("The user selected Autonomous generation and authorized Blender editing and asset operations for this scene."
                  if autonomous else "Respect the active scene permission switches.")
    return f"""Operate on the current Blender file through BlendRelay. instance_id={instance}.
Use only the MCP server named `blendrelay` for Blender operations. Do not use a different Blender server. If `blendrelay` is unavailable, report that registration problem immediately instead of inspecting local MCP wrapper files or trying another server.
Task category={mode}; workload={resource_mode}; output_quality={quality}.
Current scene state and the current user request supersede old conversation assumptions.

TASK STRATEGY:
{task_rule}
{quality_rule}
Before editing, derive a compact internal brief containing the requested result, scope, dependencies, constraints, exclusions and acceptance checks. Do not spend a tool call reporting it.
Use typed inspection and edit tools when they fit. Use execute_blender_python freely for Blender capabilities that do not have a typed tool. Return compact structured results rather than source or datablock dumps.
{permission}

Use supplied selection, sketches, target regions and reference images directly. Do not repeatedly inspect unchanged or empty data. Do not search logs, wrapper files, add-on source or the run environment while working in Blender.
Read asset policy once when external assets are relevant. Continue with procedural or existing data when no suitable permitted asset exists.

The starting allowance is {max_mcp_calls} MCP calls and {max_edit_attempts} accepted edits. It expands automatically while operations succeed. Each operation may be checked up to {max_job_polls} times while queued or running. Never poll terminal work or resubmit uncertain work with a new ID. A rejected or transport-failed submission does not consume an edit.

{mode_rule}
{'Only the selected target and dependencies required to complete the request may change.' if only_selected else 'Organize new datablocks with clear names and preserve unrelated user content.'}

VERIFICATION:
{visual_rule}
Finish when the requested Blender state and relevant acceptance checks are complete. Do not add cameras, lights, materials, scenery or render settings unless the request or chosen workflow needs them. Report real limitations concisely.
"""
