"""Deterministic JSON mesh recipes. Blender only instantiates these recipes."""
import math
from .model import entities, wall_points, room_polygon, digest, validate


def box_vertices(x, y, z, w, d, h):
    return [[x+dx*w, y+dy*d, z+dz*h] for dx,dy,dz in ((0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1))]


BOX_FACES = [[0,3,2,1],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]]


def build_specs(model):
    validate(model)
    specs = []; idx = entities(model)
    material_by_id = {m["id"]: m for m in model["materials"]}
    def emit(eid, role, vertices, faces, mat, label=None):
        spec = {"entity_id": eid, "role": role, "label": label or f"{eid} {role}",
                "vertices": vertices, "faces": faces, "material": material_by_id.get(mat, {"id": mat, "rgba": [0.7,0.7,0.7,1]})}
        spec["hash"] = digest(spec)
        specs.append(spec)
    def box(eid, role, x,y,z,w,d,h,mat, label=None, transform=None):
        vertices=box_vertices(x,y,z,w,d,h)
        if transform: vertices=[transform(v) for v in vertices]
        emit(eid,role,vertices,BOX_FACES,mat,label)
    for wall in model["walls"]:
        a,b=wall_points(model,wall); L=math.dist(a,b); H=wall["height_m"]; T=wall["thickness_m"]
        ux,uy=(b[0]-a[0])/L,(b[1]-a[1])/L
        def world(v): return [a[0]+ux*v[0]-uy*v[1],a[1]+uy*v[0]+ux*v[1],v[2]]
        openings=[o for o in model["openings"] if o["host_wall_id"]==wall["id"]]
        xx=sorted({0,L,*[v for o in openings for v in (o["offset_m"],o["offset_m"]+o["width_m"])]})
        zz=sorted({0,H,*[v for o in openings for v in (o["sill_m"],o["sill_m"]+o["height_m"])]})
        occupied=set()
        for i in range(len(xx)-1):
            for k in range(len(zz)-1):
                mx,mz=(xx[i]+xx[i+1])/2,(zz[k]+zz[k+1])/2
                if not any(o["offset_m"] < mx < o["offset_m"]+o["width_m"] and o["sill_m"] < mz < o["sill_m"]+o["height_m"] for o in openings): occupied.add((i,k))
        vertices=[]; faces=[]; lookup={}
        def face(coords):
            indices=[]
            for p in coords:
                key=tuple(round(n,8) for n in p)
                if key not in lookup: lookup[key]=len(vertices);vertices.append(world(p))
                indices.append(lookup[key])
            faces.append(indices)
        for i,k in sorted(occupied):
            x0,x1,z0,z1=xx[i],xx[i+1],zz[k],zz[k+1]; y0,y1=-T/2,T/2
            face([[x0,y0,z0],[x1,y0,z0],[x1,y0,z1],[x0,y0,z1]])
            face([[x1,y1,z0],[x0,y1,z0],[x0,y1,z1],[x1,y1,z1]])
            if (i-1,k) not in occupied: face([[x0,y1,z0],[x0,y0,z0],[x0,y0,z1],[x0,y1,z1]])
            if (i+1,k) not in occupied: face([[x1,y0,z0],[x1,y1,z0],[x1,y1,z1],[x1,y0,z1]])
            if (i,k-1) not in occupied: face([[x0,y1,z0],[x1,y1,z0],[x1,y0,z0],[x0,y0,z0]])
            if (i,k+1) not in occupied: face([[x0,y0,z1],[x1,y0,z1],[x1,y1,z1],[x0,y1,z1]])
        emit(wall["id"],"wall",vertices,faces,wall["material_id"],f"Wall {wall['id']}")
        for o in openings:
            element=next(e for e in model["windows"]+model["doors"] if e["opening_id"]==o["id"])
            is_window=element in model["windows"]; x,z,w,h=o["offset_m"],o["sill_m"],o["width_m"],o["height_m"]
            t=min(0.07,w/6,h/6)
            for role,bounds in (("frame_left",(x,-T*.55,z,t,T*1.1,h)),("frame_right",(x+w-t,-T*.55,z,t,T*1.1,h)),("frame_top",(x+t,-T*.55,z+h-t,w-2*t,T*1.1,t))):
                box(element["id"],role,*bounds,element["materials"]["frame"],element.get("label"),transform=world)
            if is_window:
                box(element["id"],"frame_bottom",x+t,-T*.55,z,w-2*t,T*1.1,t,element["materials"]["frame"],element.get("label"),transform=world)
                box(element["id"],"glass",x+t,-0.012,z+t,w-2*t,0.024,h-2*t,element["materials"]["glass"],element.get("label"),transform=world)
            else:
                box(element["id"],"leaf",x+t,-0.02,z,w-2*t,0.04,h-t,element["materials"].get("leaf","mat-wood"),element.get("label"),transform=world)
    for room in model["spaces"]:
        p=room_polygon(model,room);x0,x1=min(v[0] for v in p),max(v[0] for v in p);y0,y1=min(v[1] for v in p),max(v[1] for v in p)
        box(room["id"],"floor",x0,y0,-0.18,x1-x0,y1-y0,0.18,"mat-floor",room["label"]+" floor")
        H=model["storeys"][0]["height_m"]
        box(room["id"],"ceiling",x0,y0,H,x1-x0,y1-y0,0.08,"mat-white",room["label"]+" ceiling")
    for asset in model.get("assets",[]):
        eid=asset["id"];p=asset["parameters"];w,d,h=p["width_m"],p["depth_m"],p["height_m"];mat=asset["material_id"]
        ox,oy,oz=asset["position_m"];rot=asset.get("rotation_rad",0);c,s=math.cos(rot),math.sin(rot)
        def transform(v): return [ox+c*v[0]-s*v[1],oy+s*v[0]+c*v[1],oz+v[2]]
        def part(role,x,y,z,ww,dd,hh,m=mat): box(eid,role,x,y,z,ww,dd,hh,m,asset["label"],transform=transform)
        family=asset["family"]
        if family in ("table","chair"):
            part("top",-w/2,-d/2,h-.07,w,d,.07)
            for i,(x,y) in enumerate(((-w/2,-d/2),(w/2-.07,-d/2),(-w/2,d/2-.07),(w/2-.07,d/2-.07))):part(f"leg_{i}",x,y,0,.07,.07,h-.07)
            if family=="chair":part("back",-w/2,d/2-.07,h,w,.07,h*.65)
        elif family=="bed":
            part("base",-w/2,-d/2,0,w,d,h*.5)
            part("mattress",-w/2,-d/2,h*.5,w,d,h*.5,"mat-fabric")
            part("headboard",-w/2,d/2-.06,0,w,.08,h*1.6)
        else:
            part("back",-w/2,d/2-.04,0,w,.04,h)
            part("left",-w/2,-d/2,0,.04,d,h);part("right",w/2-.04,-d/2,0,.04,d,h)
            part("top",-w/2,-d/2,h-.04,w,d,.04);part("bottom",-w/2,-d/2,0,w,d,.04)
            n=p.get("drawers",3)
            for i in range(n):
                z=.05+i*(h-.1)/n
                if family=="cabinet":part(f"drawer_{i}",-w/2+.045,-d/2-.005,z,w-.09,.04,(h-.1)/n-.015)
                else:part(f"shelf_{i}",-w/2+.04,-d/2,z,w-.08,d-.04,.03)
    roof=model.get("roof",{"kind":"none"})
    if roof["kind"]!="none":
        p=[v["xy_m"] for v in model["vertices"]];over=roof["overhang_m"]
        x0,x1=min(v[0] for v in p)-over,max(v[0] for v in p)+over;y0,y1=min(v[1] for v in p)-over,max(v[1] for v in p)+over
        H=max(w["height_m"] for w in model["walls"])+.08
        if roof["kind"]=="flat":box("roof","roof",x0,y0,H,x1-x0,y1-y0,.15,roof["material_id"],"Flat roof")
        else:
            xm=(x0+x1)/2;peak=H+(x1-x0)/2*math.tan(math.radians(roof["pitch_degrees"]))
            for role,xa,xb,za,zb in (("roof_left",x0,xm,H,peak),("roof_right",xm,x1,peak,H)):
                verts=[[xa,y0,za],[xb,y0,zb],[xb,y1,zb],[xa,y1,za],[xa,y0,za+.12],[xb,y0,zb+.12],[xb,y1,zb+.12],[xa,y1,za+.12]]
                emit("roof",role,verts,BOX_FACES,roof["material_id"],"Gable roof")
    return specs


def plan_svg(model):
    import html
    pts=[v["xy_m"] for v in model["vertices"]];x0,x1=min(p[0] for p in pts),max(p[0] for p in pts);y0,y1=min(p[1] for p in pts),max(p[1] for p in pts)
    scale=60; margin=50; W=(x1-x0)*scale+2*margin;H=(y1-y0)*scale+2*margin
    def pos(x,y):return (margin+(x-x0)*scale,margin+(y1-y)*scale)
    lines=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">', '<rect width="100%" height="100%" fill="#faf8f3"/>']
    for room in model["spaces"]:
        p=room_polygon(model,room);xy=[pos(*v) for v in p];cx=sum(v[0] for v in xy)/len(xy);cy=sum(v[1] for v in xy)/len(xy)
        lines.append('<polygon points="'+ ' '.join(f'{x},{y}' for x,y in xy)+'" fill="#e6e2d6" stroke="none"/>')
        lines.append(f'<text x="{cx}" y="{cy}" text-anchor="middle" font-family="sans-serif" font-size="13">{html.escape(room["label"])}</text>')
    for wall in model["walls"]:
        a,b=wall_points(model,wall);u,v=pos(*a),pos(*b)
        lines.append(f'<line x1="{u[0]}" y1="{u[1]}" x2="{v[0]}" y2="{v[1]}" stroke="#253c43" stroke-width="{wall["thickness_m"]*scale}"/>')
    for o in model["openings"]:
        wall=entities(model)[o["host_wall_id"]];a,b=wall_points(model,wall);L=math.dist(a,b);dx,dy=(b[0]-a[0])/L,(b[1]-a[1])/L
        u=pos(a[0]+dx*o["offset_m"],a[1]+dy*o["offset_m"]);v=pos(a[0]+dx*(o["offset_m"]+o["width_m"]),a[1]+dy*(o["offset_m"]+o["width_m"]))
        lines.append(f'<line x1="{u[0]}" y1="{u[1]}" x2="{v[0]}" y2="{v[1]}" stroke="#6bb6c5" stroke-width="{wall["thickness_m"]*scale+1}"/>')
    lines.append(f'<text x="{W/2}" y="25" text-anchor="middle" font-family="sans-serif" font-size="16">ArchForge · {x1-x0:.2f} m × {y1-y0:.2f} m · wall centrelines</text></svg>')
    return '\n'.join(lines)
