#!/usr/bin/env python3
"""Give the Orca pet a speech bubble fed by the `heidi.codepipeline` plugin.

Usage: orca-pet-speech-bubble.py <app.asar>   (rewritten in place)

Upstream (<= 1.4.224) has no pet speech bubble (stablyai/orca#26449 is still
open and only speaks about agent status). This injects a renderer-only bubble
component, `HeidiPetBubble`, into renderer/assets/PetOverlay-*.js. It polls the
plugin command `heidi.codepipeline` / `pet-feed` every 5 s with
{cursor, focused} through window.api.plugins.invokeCommand (60 s back-off while
the call throws, e.g. plugin disabled) and shows the returned team-activity
messages:

  - one bubble at a time, FIFO with tier 1 jumping the queue; tier 1 shows
    15 s, others 10 s; hover (and dragging) pauses the timer; `+N` badge when
    more are waiting
  - body click opens `url` via window.api.shell.openUrl and dismisses; x
    dismisses only
  - while the window is hidden/unfocused nothing shows (a visible bubble goes
    back to the head of the queue); on return > 2 waiting messages collapse
    into one "While you were away: N updates" bubble (detail: first 3 texts),
    status bubbles are kept
  - one bubble per change of status.state to paused/error (status.text)
  - placed above the pet, right-aligned with it; flips below when there is no
    room above, left-aligns when it would overflow the left edge, clamped to
    the viewport; hidden while the pet is dragged; role=status aria-live=polite;
    theme classes only (all present in the shipped CSS); React text only

PetOverlay's root `aria-hidden` / `pointer-events-none` box is wrapped in a
Fragment so the bubble renders as its sibling (accessible, clickable). The
interval and timers are cleared on unmount. React and jsx-runtime are the
bundle's own bindings, captured by regex (minified names change per build).

Anchors (each must match exactly once):
  react / jsx-runtime chunk imports and their `var X=e(t())` namespaces
  `{dragging:u,dragAnimation:…,handlers:…}=E(` in the PetOverlay function
  `return(0,j.jsx)(`div`,{"aria-hidden":!0,className:`pointer-events-none
   fixed z-40`,style:{left:c.x,top:c.y,width:a,height:a},children:…}` up to
   `}var G=W;export{W as PetOverlay,`

The `web/` bundles (browser-hosted Orca) are left alone.

Any anchor mismatch, or an asar that already contains HeidiPetBubble, exits
non-zero and the Nix build fails: after an Orca bump, re-check the anchors.

The asar is rewritten without repacking: only the patched file's bytes,
offsets and integrity hashes change; `unpacked` entries and everything else are
kept as they are.
"""
import hashlib
import json
import re
import struct
import sys

BLOCK = 4 * 1024 * 1024
NAME = "orca-pet-speech-bubble"

# Injected after the PetOverlay function. __R__ / __J__ become the bundle's
# React and jsx-runtime namespaces. The pure:* markers let a test harness
# evaluate the queue/placement logic from the patched bytes.
BUBBLE = r"""/*heidi-pet-bubble:begin*/var heidiPetR=__R__,heidiPetJ=__J__;
var HEIDI_PET_FEED={pluginKey:`heidi.codepipeline`,commandId:`pet-feed`,pollMs:5e3,backoffMs:6e4};
/*heidi-pet-bubble:pure:begin*/
function heidiPetMsg(m){if(!m||typeof m!=`object`||typeof m.id!=`string`||typeof m.text!=`string`||!m.text)return null;return{id:m.id,tier:m.tier===1||m.tier===3?m.tier:2,text:m.text,detail:typeof m.detail==`string`&&m.detail?m.detail:void 0,url:typeof m.url==`string`&&/^https?:\/\//.test(m.url)?m.url:void 0}}
function heidiPetInit(focused){return{queue:[],current:null,focused:!!focused,away:!focused,status:`ok`,seq:0}}
function heidiPetTake(q){let i=q.findIndex(m=>m.tier===1);i<0&&(i=0);return[q[i],q.slice(0,i).concat(q.slice(i+1))]}
function heidiPetAway(q){let o=q.filter(m=>!m.status);if(o.length<=2)return q;return[{id:`away-${o[0].id}`,tier:2,text:`While you were away: ${o.length} updates`,detail:o.slice(0,3).map(m=>m.text).join(` · `)}].concat(q.filter(m=>m.status))}
function heidiPetShow(s){if(s.current||!s.focused||!s.queue.length)return s;let[c,q]=heidiPetTake(s.queue);return{...s,current:c,queue:q}}
function heidiPetReduce(s,a){switch(a.type){
case`feed`:{let have=new Set(s.queue.map(m=>m.id)),q=s.queue,st=s.status,seq=s.seq;s.current&&have.add(s.current.id);
let add=[];for(let r of Array.isArray(a.messages)?a.messages:[]){let m=heidiPetMsg(r);m&&!have.has(m.id)&&(have.add(m.id),add.push(m))}
let ns=a.status&&typeof a.status.state==`string`?a.status.state:st;
if(ns!==st){seq++;ns===`ok`&&(q=q.filter(m=>!m.status));(ns===`paused`||ns===`error`)&&add.push({id:`status-${seq}`,tier:2,status:!0,text:typeof a.status.text==`string`&&a.status.text?a.status.text:ns===`paused`?`Team feed paused`:`Team feed error`})}
return heidiPetShow({...s,queue:q.concat(add),status:ns,seq})}
case`focus`:{let f=!!a.focused;if(f===s.focused)return s;
if(!f)return{...s,focused:!1,away:!0,current:null,queue:s.current?[s.current].concat(s.queue):s.queue};
return heidiPetShow({...s,focused:!0,away:!1,queue:s.away?heidiPetAway(s.queue):s.queue})}
case`dismiss`:return!s.current||a.id!==void 0&&a.id!==s.current.id?s:heidiPetShow({...s,current:null});
default:return s}}
function heidiPetPlace(x,y,size,w,h,vw,vh){let g=10,m=8,below=!1,left=x+size-w,top=y-g-h;
top<m&&(top=y+size+g,below=!0);left<m&&(left=x);
left=Math.max(m,Math.min(left,vw-w-m));top=Math.max(m,Math.min(top,vh-h-m));
return{left,top,below,tail:Math.max(14,Math.min(x+size/2-left,w-14))}}
/*heidi-pet-bubble:pure:end*/
function heidiPetFocused(){return typeof document<`u`&&document.hasFocus()&&document.visibilityState===`visible`}
function HeidiPetBubble({x,y,size,dragging}){let R=heidiPetR,J=heidiPetJ;
let[s,d]=(0,R.useReducer)(heidiPetReduce,null,()=>heidiPetInit(heidiPetFocused())),[hoverId,setHoverId]=(0,R.useState)(null),[box,setBox]=(0,R.useState)(null),ref=(0,R.useRef)(null),timer=(0,R.useRef)({id:null,left:0});
(0,R.useEffect)(()=>{let f=()=>d({type:`focus`,focused:heidiPetFocused()});window.addEventListener(`focus`,f),window.addEventListener(`blur`,f),document.addEventListener(`visibilitychange`,f),f();return()=>{window.removeEventListener(`focus`,f),window.removeEventListener(`blur`,f),document.removeEventListener(`visibilitychange`,f)}},[]);
(0,R.useEffect)(()=>{let alive=!0,t=0,cursor=null,tick=async()=>{let wait=HEIDI_PET_FEED.pollMs,focused=heidiPetFocused();d({type:`focus`,focused});
try{let r=await window.api.plugins.invokeCommand({pluginKey:HEIDI_PET_FEED.pluginKey,commandId:HEIDI_PET_FEED.commandId,args:{cursor,focused}});if(!alive)return;r&&typeof r==`object`&&(typeof r.cursor==`string`&&(cursor=r.cursor),d({type:`feed`,messages:r.messages,status:r.status}))}catch{wait=HEIDI_PET_FEED.backoffMs}
alive&&(t=setTimeout(tick,wait))};tick();return()=>{alive=!1,clearTimeout(t)}},[]);
let cur=s.current,shown=!!cur&&!dragging&&s.focused,running=shown&&hoverId!==cur.id;
(0,R.useEffect)(()=>{if(!cur)return;let tm=timer.current;tm.id!==cur.id&&(tm.id=cur.id,tm.left=cur.tier===1?15e3:1e4);if(!running)return;let start=Date.now(),h=setTimeout(()=>d({type:`dismiss`,id:cur.id}),tm.left);return()=>{clearTimeout(h),tm.left=Math.max(0,tm.left-(Date.now()-start))}},[cur,running]);
(0,R.useLayoutEffect)(()=>{let e=ref.current;if(!e)return;let w=e.offsetWidth,h=e.offsetHeight;(!box||box.w!==w||box.h!==h)&&setBox({w,h})});
let vw=window.innerWidth,vh=window.innerHeight,p=heidiPetPlace(x,y,size,box?.w??320,box?.h??56,vw,vh),more=s.queue.length,dismiss=()=>d({type:`dismiss`,id:cur.id});
return(0,J.jsx)(`div`,{ref,role:`status`,"aria-live":`polite`,className:`fixed z-40`,style:{left:p.left,top:p.top,width:`max-content`,maxWidth:Math.max(120,Math.min(320,vw-16)),visibility:shown?`visible`:`hidden`,pointerEvents:shown?`auto`:`none`},children:cur?(0,J.jsxs)(`div`,{className:`relative rounded-lg border border-border bg-popover text-popover-foreground shadow-floating`,onMouseEnter:()=>setHoverId(cur.id),onMouseLeave:()=>setHoverId(null),children:[
(0,J.jsxs)(`button`,{type:`button`,title:cur.url,onClick:()=>{cur.url&&window.api.shell.openUrl(cur.url),dismiss()},style:{display:`block`,width:`100%`,textAlign:`left`,padding:`8px 26px 8px 10px`,background:`transparent`,border:0,color:`inherit`,font:`inherit`,cursor:cur.url?`pointer`:`default`},children:[(0,J.jsx)(`div`,{className:`text-xs font-medium leading-snug break-words`,children:cur.text}),cur.detail?(0,J.jsx)(`div`,{className:`text-xs leading-snug break-words text-muted-foreground`,style:{marginTop:2},children:cur.detail}):null]}),
(0,J.jsx)(`button`,{type:`button`,"aria-label":`Dismiss`,className:`text-muted-foreground hover:text-foreground`,onClick:dismiss,style:{position:`absolute`,top:3,right:5,padding:`0 3px`,background:`transparent`,border:0,cursor:`pointer`,fontSize:14,lineHeight:1},children:`×`}),
more>0?(0,J.jsx)(`span`,{className:`rounded-full bg-primary text-primary-foreground tabular-nums font-semibold`,"aria-label":`${more} more`,style:{position:`absolute`,top:-8,left:-8,minWidth:16,padding:`0 5px`,fontSize:10,lineHeight:`16px`,textAlign:`center`},children:`+${more}`}):null,
(0,J.jsx)(`div`,{"aria-hidden":!0,className:`bg-popover border-border`,style:{position:`absolute`,left:p.tail-5,width:10,height:10,transform:`rotate(45deg)`,borderStyle:`solid`,borderWidth:0,...(p.below?{top:-6,borderLeftWidth:1,borderTopWidth:1}:{bottom:-6,borderRightWidth:1,borderBottomWidth:1})}})]}):null})}
/*heidi-pet-bubble:end*/"""


def sub_once(name, pattern, repl, text):
    out, n = re.subn(pattern, repl, text, flags=re.S)
    if n != 1:
        sys.exit(f"{NAME}: anchor '{name}' matched {n} times (expected 1)")
    return out


def find_once(name, pattern, text):
    found = re.findall(pattern, text, flags=re.S)
    if len(found) != 1:
        sys.exit(f"{NAME}: anchor '{name}' matched {len(found)} times (expected 1)")
    return found[0]


def patch_pet_overlay(js):
    if "HeidiPetBubble" in js:
        sys.exit(f"{NAME}: PetOverlay already contains HeidiPetBubble (asar already patched?)")
    react_import = find_once("react import", r'import\{t as (\w+)\}from"\./react-[\w-]+\.js"', js)
    react = find_once("react namespace", rf"var (\w+)=\w+\({re.escape(react_import)}\(\)\)", js)
    jsx_import = find_once("jsx-runtime import", r'import\{t as (\w+)\}from"\./jsx-runtime-[\w-]+\.js"', js)
    jsx = find_once("jsx-runtime namespace", rf"var (\w+)=\w+\({re.escape(jsx_import)}\(\)\)", js)
    dragging = find_once(
        "drag state",
        r"\{dragging:(\w+),dragAnimation:\w+,hovering:\w+,dragGeneration:\w+,handlers:\w+\}=\w+\(", js)
    bubble = BUBBLE.replace("__R__", react).replace("__J__", jsx)
    j = re.escape(jsx)
    return sub_once(
        "pet root",
        rf"return(\(0,{j}\.jsx\)\(`div`,\{{\"aria-hidden\":!0,className:`pointer-events-none fixed z-40`,"
        rf"style:\{{left:(\w+)\.x,top:\2\.y,width:(\w+),height:\3\}},children:.*?)\}}"
        rf"(var \w+=(\w+);export\{{\5 as PetOverlay,)",
        lambda m: f"return(0,{jsx}.jsxs)({jsx}.Fragment,{{children:[{m[1]},(0,{jsx}.jsx)(HeidiPetBubble,"
        f"{{x:{m[2]}.x,y:{m[2]}.y,size:{m[3]},dragging:{dragging}}})]}})}}{bubble}{m[4]}",
        js,
    )


TARGETS = {
    "pet": (r"out/renderer/assets/PetOverlay-[\w-]+\.js", patch_pet_overlay),
}


def target(path):
    for key, (pattern, fn) in TARGETS.items():
        if re.fullmatch(pattern, path):
            return key, fn
    return None, None


def integrity(data):
    blocks = [hashlib.sha256(data[i:i + BLOCK]).hexdigest() for i in range(0, len(data), BLOCK)] or [
        hashlib.sha256(b"").hexdigest()]
    return {"algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(), "blockSize": BLOCK, "blocks": blocks}


def main(asar_path):
    with open(asar_path, "rb") as f:
        raw = f.read()
    # Chromium pickle: [4][header pickle size] then pickle([payload size][json len][json][pad]).
    (header_pickle_size,) = struct.unpack_from("<I", raw, 4)
    (json_len,) = struct.unpack_from("<I", raw, 12)
    header = json.loads(raw[16:16 + json_len])
    base = 8 + header_pickle_size

    files = []  # (path, node) for packed files, in archive order
    def walk(node, prefix):
        for name, child in node["files"].items():
            p = f"{prefix}{name}"
            if "files" in child:
                walk(child, p + "/")
            elif "offset" in child and not child.get("unpacked"):
                files.append((p, child))
    walk(header, "")
    files.sort(key=lambda e: int(e[1]["offset"]))

    patched = []
    chunks, offset = [], 0
    for path, node in files:
        start = base + int(node["offset"])
        data = raw[start:start + node["size"]]
        key, fn = target(path)
        if fn:
            data = fn(data.decode("utf-8")).encode("utf-8")
            node["integrity"] = integrity(data)
            patched.append(key)
        node["offset"] = str(offset)
        node["size"] = len(data)
        chunks.append(data)
        offset += len(data)
    if sorted(patched) != sorted(TARGETS):
        sys.exit(f"{NAME}: expected one each of {sorted(TARGETS)}, patched {sorted(patched)}")

    js = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(js) % 4) % 4
    payload = struct.pack("<I", len(js)) + js + b"\0" * pad
    header_pickle = struct.pack("<I", len(payload)) + payload
    with open(asar_path, "wb") as f:
        f.write(struct.pack("<II", 4, len(header_pickle)))
        f.write(header_pickle)
        for c in chunks:
            f.write(c)
    print(f"{NAME}: patched {asar_path}")


if __name__ == "__main__":
    main(sys.argv[1])
