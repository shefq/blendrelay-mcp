"""Versioned orthogonal residential models. All distances are metres."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid


class DomainError(ValueError):
    def __init__(self, code, message, entity_ids=(), **details):
        super().__init__(message)
        self.code, self.entity_ids, self.details = code, list(entity_ids), details

    def as_dict(self):
        return {"code": self.code, "message": str(self), "entity_ids": self.entity_ids,
                "retryable": False, "details": self.details}


def uid(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def number(value, name, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise DomainError("INVALID_NUMBER", f"{name} must be a finite number")
    if low is not None and value < low or high is not None and value > high:
        raise DomainError("OUT_OF_RANGE", f"{name} must be between {low} and {high}")
    return float(value)


def strict(obj, allowed, required=()):
    if not isinstance(obj, dict):
        raise DomainError("INVALID_INPUT", "Expected an object")
    extra, missing = set(obj) - set(allowed), set(required) - set(obj)
    if extra or missing:
        raise DomainError("INVALID_FIELDS", f"Unexpected fields: {sorted(extra)}; missing: {sorted(missing)}")


PALETTE = {
    "plaster": [0.8, 0.76, 0.67, 1.0], "wood": [0.40, 0.21, 0.085, 1.0],
    "floor": [0.58, 0.4, 0.24, 1.0], "white": [0.88, 0.9, 0.9, 1.0],
    "black": [0.022, 0.027, 0.035, 1.0], "glass": [0.35, 0.65, 0.8, 0.30],
    "roof": [0.10, 0.15, 0.19, 1.0], "fabric": [0.22, 0.40, 0.41, 1.0],
    "red": [0.6, 0.035, 0.025, 1.0], "blue": [0.06, 0.20, 0.55, 1.0],
    "green": [0.15, 0.35, 0.15, 1.0], "grey": [0.35, 0.38, 0.4, 1.0],
}


def entities(model):
    return {e["id"]: e for group in ("vertices", "edges", "walls", "spaces", "openings",
                                   "windows", "doors", "assets", "materials") for e in model.get(group, [])}


def wall_points(model, wall):
    idx = entities(model)
    edge = idx[wall["edge_id"]]
    return idx[edge["start_vertex_id"]]["xy_m"], idx[edge["end_vertex_id"]]["xy_m"]


def room_polygon(model, room):
    idx = entities(model)
    return [idx[idx[s["edge_id"]]["start_vertex_id" if s["direction"] == "forward" else "end_vertex_id"]]["xy_m"]
            for s in room["boundary"]]


def room_area(model, room):
    p = room_polygon(model, room)
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(p, p[1:] + p[:1]))) / 2


def from_rectangles(rectangles, *, name="ArchForge house", height=2.8, roof="gable", furnish=True):
    """Compile tiled, axis-aligned room rectangles into shared boundary edges."""
    if not rectangles or len(rectangles) > 40:
        raise DomainError("LAYOUT_LIMIT", "Supply between 1 and 40 rectangular spaces")
    number(height, "wall height", 2.0, 6.0)
    if roof not in ("flat", "gable", "none"):
        raise DomainError("UNSUPPORTED_ROOF", "Choose flat, gable, or none")
    for r in rectangles:
        strict(r, ("name", "x", "y", "width", "depth"), ("name", "x", "y", "width", "depth"))
        number(r["x"], "x", -100, 100); number(r["y"], "y", -100, 100)
        number(r["width"], "room width", 0.8, 50); number(r["depth"], "room depth", 0.8, 50)
    for i, a in enumerate(rectangles):
        for b in rectangles[i + 1:]:
            if min(a["x"] + a["width"], b["x"] + b["width"]) - max(a["x"], b["x"]) > 1e-6 and min(a["y"] + a["depth"], b["y"] + b["depth"]) - max(a["y"], b["y"]) > 1e-6:
                raise DomainError("ROOM_OVERLAP", f"{a['name']} overlaps {b['name']}")
    xs = sorted({round(v, 6) for r in rectangles for v in (r["x"], r["x"] + r["width"])})
    ys = sorted({round(v, 6) for r in rectangles for v in (r["y"], r["y"] + r["depth"])})
    model = {"format": "archforge.project", "schema_version": "1.0.0", "project_id": uid("project"),
             "name": name[:120], "revision": 1, "units": {"length": "m", "angle": "rad"},
             "seed": 1, "vertices": [], "edges": [], "walls": [], "spaces": [], "openings": [],
             "windows": [], "doors": [], "assets": [], "materials": [
                 {"id": f"mat-{k}", "label": k.title(), "rgba": v} for k, v in PALETTE.items()],
             "storeys": [{"id": "ground", "elevation_m": 0, "height_m": height}],
             "roof": {"kind": roof, "pitch_degrees": 25.0, "overhang_m": 0.3, "material_id": "mat-roof"},
             "protected_ids": [], "assumptions": [], "sources": []}
    vmap, emap, usage = {}, {}, {}
    def vertex(p):
        p = tuple(round(v, 6) for v in p)
        if p not in vmap:
            vmap[p] = f"v-{len(vmap)+1}"
            model["vertices"].append({"id": vmap[p], "storey_id": "ground", "xy_m": list(p)})
        return vmap[p]
    def segment(a, b):
        a=tuple(round(v,6) for v in a);b=tuple(round(v,6) for v in b)
        key = tuple(sorted((a,b)))
        if key not in emap:
            eid = f"edge-{len(emap)+1}"
            emap[key] = eid
            model["edges"].append({"id": eid, "start_vertex_id": vertex(key[0]), "end_vertex_id": vertex(key[1])})
            usage[eid] = []
        return {"edge_id": emap[key], "direction": "forward" if tuple(a) == key[0] else "reverse"}
    for n, r in enumerate(rectangles):
        x, y, w, d = (r[k] for k in ("x", "y", "width", "depth"))
        xx = [v for v in xs if x-1e-6 <= v <= x+w+1e-6]
        yy = [v for v in ys if y-1e-6 <= v <= y+d+1e-6]
        points = [(v, y) for v in xx] + [(x+w, v) for v in yy[1:]] + [(v, y+d) for v in xx[-2::-1]] + [(x, v) for v in yy[-2:0:-1]]
        room = {"id": f"room-{n+1}", "label": r["name"][:80], "storey_id": "ground", "boundary": [segment(a, b) for a, b in zip(points, points[1:] + points[:1])]}
        model["spaces"].append(room)
        for s in room["boundary"]: usage[s["edge_id"]].append(room["id"])
    for edge in model["edges"]:
        exterior = len(usage[edge["id"]]) == 1
        model["walls"].append({"id": edge["id"].replace("edge", "wall"), "edge_id": edge["id"],
            "height_m": height, "thickness_m": 0.2 if exterior else 0.12, "alignment": "centre",
            "material_id": "mat-plaster", "space_ids": usage[edge["id"]], "exterior": exterior})
    # One opening per eligible wall; corridor doors give each room access.
    idx = entities(model)
    connected = set()
    for wall in model["walls"]:
        a, b = wall_points(model, wall); length = math.dist(a, b)
        rooms = [idx[r] for r in wall["space_ids"]]
        corridor = any("corridor" in r["label"].lower() for r in rooms)
        if not wall["exterior"] and corridor and length > 1.15:
            other = next((r for r in rooms if "corridor" not in r["label"].lower()), rooms[0])
            if other["id"] not in connected:
                add_opening(model, wall, "door", width=0.9)
                connected.add(other["id"])
        elif wall["exterior"] and length > 1.15:
            if corridor and abs(a[1] - b[1]) < 1e-6:
                add_opening(model, wall, "door", width=min(0.9, length-0.3))
            elif not corridor:
                add_opening(model, wall, "window", width=min(1.5, length-0.5))
    # A single-room concept still needs an entrance.
    if not any(idx.get(w["host_wall_id"], {}).get("exterior") for w in model["openings"] if w["sill_m"] == 0):
        candidates = [w for w in model["walls"] if w["exterior"] and math.dist(*wall_points(model, w)) > 2.8]
        if candidates:
            wall = candidates[0]
            remove = {o["id"] for o in model["openings"] if o["host_wall_id"] == wall["id"]}
            model["openings"] = [o for o in model["openings"] if o["id"] not in remove]
            model["windows"] = [w for w in model["windows"] if w["opening_id"] not in remove]
            add_opening(model, wall, "door", width=0.95)
    if furnish:
        for room in model["spaces"]:
            if "corridor" in room["label"].lower(): continue
            p = room_polygon(model, room)
            x0, x1 = min(p[0] for p in p), max(p[0] for p in p)
            y0, y1 = min(p[1] for p in p), max(p[1] for p in p)
            label = room["label"].lower()
            family = "bed" if "bed" in label else "cabinet" if any(t in label for t in ("kitchen", "bath")) else "table"
            width = min(1.6, x1-x0-0.7); depth = min(1.9 if family == "bed" else 0.8, y1-y0-0.7)
            if width > 0.5 and depth > 0.4:
                model["assets"].append({"id": uid("asset"), "label": f"{room['label']} {family}", "family": family,
                    "room_id": room["id"], "position_m": [(x0+x1)/2, (y0+y1)/2, 0], "rotation_rad": 0,
                    "parameters": {"width_m": width, "depth_m": depth, "height_m": 0.55 if family == "bed" else 0.8, "drawers": 3}, "material_id": "mat-wood"})
    validate(model)
    return model


def add_opening(model, wall, kind, width):
    length = math.dist(*wall_points(model, wall))
    oid = uid("opening")
    model["openings"].append({"id": oid, "host_wall_id": wall["id"], "offset_m": (length-width)/2,
        "width_m": width, "height_m": 2.1 if kind == "door" else 1.15, "sill_m": 0 if kind == "door" else 0.9, "anchor": "centre_fixed"})
    model[kind + "s"].append({"id": uid(kind), "label": f"{kind.title()} {wall['id']}", "opening_id": oid,
        "materials": {"frame": "mat-white", "glass" if kind == "window" else "leaf": "mat-glass" if kind == "window" else "mat-wood"}})


def house(width=12.0, depth=10.0, bedrooms=3, height=2.8, roof="gable", furnish=True, name="ArchForge house"):
    width = number(width, "house width", 6, 40); depth = number(depth, "house depth", 6, 40)
    if isinstance(bedrooms, bool) or not isinstance(bedrooms, int) or not 1 <= bedrooms <= 8:
        raise DomainError("OUT_OF_RANGE", "Bedrooms must be an integer from 1 to 8")
    corridor = 1.3
    names = ["Living room", "Kitchen", "Bathroom"] + [f"Bedroom {i+1}" for i in range(bedrooms)]
    groups = [names[::2], names[1::2]]
    room_width = (width-corridor)/2
    rooms = [{"name": "Corridor", "x": room_width, "y": 0, "width": corridor, "depth": depth}]
    for side, labels in enumerate(groups):
        d = depth/len(labels)
        if d < 2.0: raise DomainError("INFEASIBLE_LAYOUT", "Increase house depth for the requested room count")
        for i, label in enumerate(labels):
            rooms.append({"name": label, "x": 0 if side == 0 else room_width+corridor,
                          "y": i*d, "width": room_width, "depth": d})
    return from_rectangles(rooms, height=height, roof=roof, furnish=furnish, name=name)


def parse_brief(prompt):
    """Deliberately bounded native planner; MCP hosts can supply richer structured plans."""
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
        raise DomainError("INVALID_PROMPT", "Supply a short house description")
    args, assumptions = {}, []
    dims = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:m|metres?|meters?)\b", prompt, re.I)
    if dims: args.update(width=float(dims[1]), depth=float(dims[2]))
    else: assumptions.append("Using a 12 m × 10 m centreline footprint; provide dimensions to change it.")
    count = re.search(r"\b([1-8])\s*(?:bedrooms?|beds?)\b", prompt, re.I)
    args["bedrooms"] = int(count[1]) if count else 3
    if not count: assumptions.append("Using 3 bedrooms plus living room, kitchen, bathroom, and corridor.")
    args["roof"] = "flat" if "flat" in prompt.lower() else "gable"
    assumptions.append("One storey; template layout with 2.8 m walls. Structural and services design is not included.")
    if re.search(r"\b(two|three|[2-9])[- ](?:storey|story|floor)", prompt, re.I):
        raise DomainError("UNSUPPORTED_STOREYS", "This release generates one storey; use a one-storey brief")
    model = house(**args)
    model["assumptions"] = assumptions
    model["brief"] = prompt
    return model


def validate(model):
    if not isinstance(model, dict) or model.get("format") != "archforge.project" or model.get("schema_version") != "1.0.0":
        raise DomainError("UNSUPPORTED_SCHEMA", "Expected archforge.project schema 1.0.0")
    idx = entities(model)
    groups = ("vertices", "edges", "walls", "spaces", "openings", "windows", "doors", "assets", "materials")
    if sum(len(model.get(g, [])) for g in groups) != len(idx):
        raise DomainError("DUPLICATE_ID", "Entity IDs must be unique")
    if len(idx) > 3000: raise DomainError("RESOURCE_LIMIT", "This release supports at most 3000 semantic records")
    materials = {m["id"] for m in model["materials"]}
    for vertex in model["vertices"]:
        if len(vertex["xy_m"]) != 2: raise DomainError("INVALID_VERTEX", "Vertices need XY coordinates")
        for n in vertex["xy_m"]: number(n, "coordinate", -100, 100)
    for wall in model["walls"]:
        try: a, b = wall_points(model, wall)
        except (KeyError, TypeError): raise DomainError("BROKEN_REFERENCE", "Wall edge/vertex is missing", [wall["id"]])
        if math.dist(a, b) < 0.05: raise DomainError("SHORT_WALL", "Wall is too short", [wall["id"]])
        if abs(a[0]-b[0]) > 1e-5 and abs(a[1]-b[1]) > 1e-5:
            raise DomainError("NON_ORTHOGONAL_WALL", "This release supports axis-aligned walls", [wall["id"]])
        number(wall["height_m"], "wall height", 0.5, 10); number(wall["thickness_m"], "wall thickness", 0.05, 1)
        if wall["material_id"] not in materials: raise DomainError("UNKNOWN_MATERIAL", "Wall material is missing")
    for space in model["spaces"]:
        chain = []
        for segment in space["boundary"]:
            edge = idx.get(segment["edge_id"])
            if not edge or segment["direction"] not in ("forward", "reverse"):
                raise DomainError("BROKEN_BOUNDARY", "Room boundary references a missing edge")
            ends = [edge["start_vertex_id"], edge["end_vertex_id"]]
            chain.append(ends if segment["direction"] == "forward" else ends[::-1])
        if len(chain) < 4 or any(a[1] != b[0] for a, b in zip(chain, chain[1:]+chain[:1])):
            raise DomainError("OPEN_ROOM_LOOP", "Room boundary must close", [space["id"]])
        if room_area(model, space) < 0.5: raise DomainError("ROOM_TOO_SMALL", "Room area is too small")
        polygon=room_polygon(model,space)
        bounds_area=(max(p[0] for p in polygon)-min(p[0] for p in polygon))*(max(p[1] for p in polygon)-min(p[1] for p in polygon))
        if abs(room_area(model,space)-bounds_area)>1e-5:
            raise DomainError("UNSUPPORTED_ROOM_SHAPE", "This release requires rectangular rooms", [space["id"]])
    for opening in model["openings"]:
        wall = idx.get(opening["host_wall_id"])
        if wall is None or "edge_id" not in wall: raise DomainError("BROKEN_HOST", "Opening host is missing")
        length = math.dist(*wall_points(model, wall))
        w = number(opening["width_m"], "opening width", 0.2, 20)
        h = number(opening["height_m"], "opening height", 0.2, 10)
        o = number(opening["offset_m"], "opening offset", 0, 100)
        s = number(opening["sill_m"], "sill", 0, 10)
        if o < 0.10-1e-6 or o+w > length-0.10+1e-6 or s+h > wall["height_m"]-0.05+1e-6:
            raise DomainError("OPENING_OUTSIDE_HOST", "Opening exceeds the host wall or required edge clearance", [opening["id"], wall["id"]])
        for other in model["openings"]:
            if other["id"] >= opening["id"] or other["host_wall_id"] != wall["id"]: continue
            if min(o+w, other["offset_m"]+other["width_m"])-max(o, other["offset_m"]) > 1e-6 and min(s+h, other["sill_m"]+other["height_m"])-max(s, other["sill_m"]) > 1e-6:
                raise DomainError("OPENING_OVERLAP", "Openings overlap", [opening["id"], other["id"]])
    for entity in model["windows"]+model["doors"]:
        if entity["opening_id"] not in {o["id"] for o in model["openings"]}: raise DomainError("BROKEN_HOST", "Missing opening")
        if any(v not in materials for v in entity["materials"].values()): raise DomainError("UNKNOWN_MATERIAL", "Missing finish")
    for asset in model.get("assets", []):
        if asset["family"] not in ("table", "chair", "bed", "cabinet", "shelf"):
            raise DomainError("UNSUPPORTED_FAMILY", "Unknown procedural family")
        if asset["material_id"] not in materials: raise DomainError("UNKNOWN_MATERIAL", "Missing asset material")
        for k in ("width_m", "depth_m", "height_m"): number(asset["parameters"][k], k, 0.2, 8)
        drawers = asset["parameters"].get("drawers", 3)
        if isinstance(drawers, bool) or not isinstance(drawers, int) or not 1 <= drawers <= 12: raise DomainError("OUT_OF_RANGE", "Drawers must be 1–12")
        if len(asset["position_m"]) != 3: raise DomainError("INVALID_POSITION", "Position needs XYZ")
        for n in asset["position_m"]: number(n, "position", -100, 100)
        number(asset.get("rotation_rad", 0), "rotation", -100, 100)
        if asset.get('room_id'):
            room=next((r for r in model['spaces'] if r['id']==asset['room_id']),None)
            if room is None:raise DomainError('BROKEN_REFERENCE','Asset room is missing',[asset['id']])
            points=room_polygon(model,room);x,y,_=asset['position_m'];angle=asset.get('rotation_rad',0)
            w,d=asset['parameters']['width_m'],asset['parameters']['depth_m']
            ex=(abs(math.cos(angle))*w+abs(math.sin(angle))*d)/2;ey=(abs(math.sin(angle))*w+abs(math.cos(angle))*d)/2
            if x-ex<min(p[0] for p in points)+.1 or x+ex>max(p[0] for p in points)-.1 or y-ey<min(p[1] for p in points)+.1 or y+ey>max(p[1] for p in points)-.1:
                raise DomainError('ASSET_OUTSIDE_ROOM','Asset exceeds its room clearance bounds',[asset['id'],room['id']])
    roof=model.get('roof',{'kind':'none'})
    if roof['kind'] not in ('none','flat','gable'):raise DomainError('UNSUPPORTED_ROOF','Unknown roof family')
    if roof['kind']!='none':
        number(roof['pitch_degrees'],'roof pitch',5,55);number(roof['overhang_m'],'roof overhang',0,2)
        if roof['material_id'] not in materials:raise DomainError('UNKNOWN_MATERIAL','Unknown roof material')
    owners={}
    for element in model['windows']+model['doors']:
        if element['opening_id'] in owners:raise DomainError('DUPLICATE_FILLING','Opening has multiple fillings')
        owners[element['opening_id']]=element['id']
    if set(owners)!={o['id'] for o in model['openings']}:raise DomainError('MISSING_FILLING','Each opening needs a door or window')
    canonical(model)
    return {"valid": True, "entity_count": len(idx), "room_count": len(model["spaces"]), "warnings": [
        "Areas are measured to wall centrelines; this release does not certify net usable area or building-code compliance."]}
