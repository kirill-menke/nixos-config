#!/usr/bin/env python3
"""Give the Orca pet a speech bubble fed by a plugin's `pet-feed` command.

Usage: orca-pet-speech-bubble.py <app.asar>   (rewritten in place)

Upstream (anchors checked against 1.4.263) has no pet speech bubble
(stablyai/orca#26449 is still open and only speaks about agent status). This
injects a renderer-only bubble component, `PetSpeechBubble`, into
renderer/assets/PetOverlay-*.js. It names no plugin: the feed is the first
plugin in window.api.plugins.list() that declares a `pet-feed` command and
is neither disabled nor invalid. It polls that command every 5 s with
{cursor, focused} through window.api.plugins.invokeCommand and shows the
returned team-activity messages. No such plugin, or a call that throws (e.g.
the plugin system is off): 60 s back-off, then it looks for the plugin anew.

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

Anchors (each must match exactly once; PetOverlay is React Compiler output
since 1.4.255, so its elements sit in a memo cache `e[n]`):
  react chunk import and its `var X=e(t())` namespace
  `W=(0,J.jsx)(`div`,{"aria-hidden":!0,className:`pointer-events-none fixed
   z-40`,style:M,children:U})` inside the function's last statement
   `return e[39]!==U||e[40]!==M?(W=…,e[41]=W):W=e[41],W}`; the returned `W`
   becomes the Fragment. J is the jsx-runtime namespace, which must be
   `var J=f()` with f imported from a sibling chunk (the runtime is bundled
   into a shared chunk: the client chunk up to 1.4.237, the button chunk
   since 1.4.248)
  `M={left:u.x,top:u.y,width:s,height:s}`, the root's style: position and size
  `{dragging:h,dragAnimation:…,handlers:…}=A(` in the PetOverlay function
  `export{Q as PetOverlay,`: the bubble code goes in front of it

The `web/` bundles (browser-hosted Orca) are left alone.

Any anchor mismatch, or an asar that already contains PetSpeechBubble, exits
non-zero and the Nix build fails: after an Orca bump, re-check the anchors.

The asar is rewritten without repacking: only the patched file's bytes,
offsets, sizes and integrity hashes change; everything else is kept as it is.
Since 1.4.232 the renderer bundles are `unpacked`: such targets are rewritten
in place under <app.asar>.unpacked/, and their header size/integrity are
updated to match.
"""
import hashlib
import json
import os
import re
import struct
import sys

BLOCK = 4 * 1024 * 1024
NAME = "orca-pet-speech-bubble"

# Injected after the PetOverlay function. __R__ / __J__ become the bundle's
# React and jsx-runtime namespaces. The pure:* markers let a test harness
# evaluate the queue/placement logic from the patched bytes.
BUBBLE = r"""/*pet-bubble:begin*/var petBubbleR=__R__,petBubbleJ=__J__;
var PET_BUBBLE_FEED={commandId:`pet-feed`,pollMs:5e3,backoffMs:6e4};
/*pet-bubble:pure:begin*/
function petBubbleMsg(m){if(!m||typeof m!=`object`||typeof m.id!=`string`||typeof m.text!=`string`||!m.text)return null;return{id:m.id,tier:m.tier===1||m.tier===3?m.tier:2,text:m.text,detail:typeof m.detail==`string`&&m.detail?m.detail:void 0,url:typeof m.url==`string`&&/^https?:\/\//.test(m.url)?m.url:void 0}}
function petBubbleInit(focused){return{queue:[],current:null,focused:!!focused,away:!focused,status:`ok`,seq:0}}
function petBubbleTake(q){let i=q.findIndex(m=>m.tier===1);i<0&&(i=0);return[q[i],q.slice(0,i).concat(q.slice(i+1))]}
function petBubbleAway(q){let o=q.filter(m=>!m.status);if(o.length<=2)return q;return[{id:`away-${o[0].id}`,tier:2,text:`While you were away: ${o.length} updates`,detail:o.slice(0,3).map(m=>m.text).join(` · `)}].concat(q.filter(m=>m.status))}
function petBubbleShow(s){if(s.current||!s.focused||!s.queue.length)return s;let[c,q]=petBubbleTake(s.queue);return{...s,current:c,queue:q}}
function petBubbleFeedPlugin(list){let p=(Array.isArray(list)?list:[]).find(p=>p&&typeof p.pluginKey==`string`&&p.status!==`disabled`&&p.status!==`invalid`&&Array.isArray(p.commands)&&p.commands.some(c=>c&&c.id===PET_BUBBLE_FEED.commandId));return p?p.pluginKey:null}
function petBubbleReduce(s,a){switch(a.type){
case`feed`:{let have=new Set(s.queue.map(m=>m.id)),q=s.queue,st=s.status,seq=s.seq;s.current&&have.add(s.current.id);
let add=[];for(let r of Array.isArray(a.messages)?a.messages:[]){let m=petBubbleMsg(r);m&&!have.has(m.id)&&(have.add(m.id),add.push(m))}
let ns=a.status&&typeof a.status.state==`string`?a.status.state:st;
if(ns!==st){seq++;ns===`ok`&&(q=q.filter(m=>!m.status));(ns===`paused`||ns===`error`)&&add.push({id:`status-${seq}`,tier:2,status:!0,text:typeof a.status.text==`string`&&a.status.text?a.status.text:ns===`paused`?`Team feed paused`:`Team feed error`})}
return petBubbleShow({...s,queue:q.concat(add),status:ns,seq})}
case`focus`:{let f=!!a.focused;if(f===s.focused)return s;
if(!f)return{...s,focused:!1,away:!0,current:null,queue:s.current?[s.current].concat(s.queue):s.queue};
return petBubbleShow({...s,focused:!0,away:!1,queue:s.away?petBubbleAway(s.queue):s.queue})}
case`dismiss`:return!s.current||a.id!==void 0&&a.id!==s.current.id?s:petBubbleShow({...s,current:null});
default:return s}}
function petBubblePlace(x,y,size,w,h,vw,vh){let g=10,m=8,below=!1,left=x+size-w,top=y-g-h;
top<m&&(top=y+size+g,below=!0);left<m&&(left=x);
left=Math.max(m,Math.min(left,vw-w-m));top=Math.max(m,Math.min(top,vh-h-m));
return{left,top,below,tail:Math.max(14,Math.min(x+size/2-left,w-14))}}
/*pet-bubble:pure:end*/
function petBubbleFocused(){return typeof document<`u`&&document.hasFocus()&&document.visibilityState===`visible`}
function PetSpeechBubble({x,y,size,dragging}){let R=petBubbleR,J=petBubbleJ;
let[s,d]=(0,R.useReducer)(petBubbleReduce,null,()=>petBubbleInit(petBubbleFocused())),[hoverId,setHoverId]=(0,R.useState)(null),[box,setBox]=(0,R.useState)(null),ref=(0,R.useRef)(null),timer=(0,R.useRef)({id:null,left:0});
(0,R.useEffect)(()=>{let f=()=>d({type:`focus`,focused:petBubbleFocused()});window.addEventListener(`focus`,f),window.addEventListener(`blur`,f),document.addEventListener(`visibilitychange`,f),f();return()=>{window.removeEventListener(`focus`,f),window.removeEventListener(`blur`,f),document.removeEventListener(`visibilitychange`,f)}},[]);
(0,R.useEffect)(()=>{let alive=!0,t=0,cursor=null,plugin=null,tick=async()=>{let wait=PET_BUBBLE_FEED.pollMs,focused=petBubbleFocused();d({type:`focus`,focused});
try{plugin??=petBubbleFeedPlugin(await window.api.plugins.list());if(!alive)return;if(plugin===null)wait=PET_BUBBLE_FEED.backoffMs;else{let r=await window.api.plugins.invokeCommand({pluginKey:plugin,commandId:PET_BUBBLE_FEED.commandId,args:{cursor,focused}});if(!alive)return;r&&typeof r==`object`&&(typeof r.cursor==`string`&&(cursor=r.cursor),d({type:`feed`,messages:r.messages,status:r.status}))}}catch{plugin=null,wait=PET_BUBBLE_FEED.backoffMs}
alive&&(t=setTimeout(tick,wait))};tick();return()=>{alive=!1,clearTimeout(t)}},[]);
let cur=s.current,shown=!!cur&&!dragging&&s.focused,running=shown&&hoverId!==cur.id;
(0,R.useEffect)(()=>{if(!cur)return;let tm=timer.current;tm.id!==cur.id&&(tm.id=cur.id,tm.left=cur.tier===1?15e3:1e4);if(!running)return;let start=Date.now(),h=setTimeout(()=>d({type:`dismiss`,id:cur.id}),tm.left);return()=>{clearTimeout(h),tm.left=Math.max(0,tm.left-(Date.now()-start))}},[cur,running]);
(0,R.useLayoutEffect)(()=>{let e=ref.current;if(!e)return;let w=e.offsetWidth,h=e.offsetHeight;(!box||box.w!==w||box.h!==h)&&setBox({w,h})});
let vw=window.innerWidth,vh=window.innerHeight,p=petBubblePlace(x,y,size,box?.w??320,box?.h??56,vw,vh),more=s.queue.length,dismiss=()=>d({type:`dismiss`,id:cur.id});
return(0,J.jsx)(`div`,{ref,role:`status`,"aria-live":`polite`,className:`fixed z-40`,style:{left:p.left,top:p.top,width:`max-content`,maxWidth:Math.max(120,Math.min(320,vw-16)),visibility:shown?`visible`:`hidden`,pointerEvents:shown?`auto`:`none`},children:cur?(0,J.jsxs)(`div`,{className:`relative rounded-lg border border-border bg-popover text-popover-foreground shadow-floating`,onMouseEnter:()=>setHoverId(cur.id),onMouseLeave:()=>setHoverId(null),children:[
(0,J.jsxs)(`button`,{type:`button`,title:cur.url,onClick:()=>{cur.url&&window.api.shell.openUrl(cur.url),dismiss()},style:{display:`block`,width:`100%`,textAlign:`left`,padding:`8px 26px 8px 10px`,background:`transparent`,border:0,color:`inherit`,font:`inherit`,cursor:cur.url?`pointer`:`default`},children:[(0,J.jsx)(`div`,{className:`text-xs font-medium leading-snug break-words`,children:cur.text}),cur.detail?(0,J.jsx)(`div`,{className:`text-xs leading-snug break-words text-muted-foreground`,style:{marginTop:2},children:cur.detail}):null]}),
(0,J.jsx)(`button`,{type:`button`,"aria-label":`Dismiss`,className:`text-muted-foreground hover:text-foreground`,onClick:dismiss,style:{position:`absolute`,top:3,right:5,padding:`0 3px`,background:`transparent`,border:0,cursor:`pointer`,fontSize:14,lineHeight:1},children:`×`}),
more>0?(0,J.jsx)(`span`,{className:`rounded-full bg-primary text-primary-foreground tabular-nums font-semibold`,"aria-label":`${more} more`,style:{position:`absolute`,top:-8,left:-8,minWidth:16,padding:`0 5px`,fontSize:10,lineHeight:`16px`,textAlign:`center`},children:`+${more}`}):null,
(0,J.jsx)(`div`,{"aria-hidden":!0,className:`bg-popover border-border`,style:{position:`absolute`,left:p.tail-5,width:10,height:10,transform:`rotate(45deg)`,borderStyle:`solid`,borderWidth:0,...(p.below?{top:-6,borderLeftWidth:1,borderTopWidth:1}:{bottom:-6,borderRightWidth:1,borderBottomWidth:1})}})]}):null})}
/*pet-bubble:end*/"""


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
    if "PetSpeechBubble" in js or "petBubble" in js:
        sys.exit(f"{NAME}: PetOverlay already contains PetSpeechBubble (asar already patched?)")
    react_import = find_once("react import", r'import\{t as (\w+)\}from"\./react-[\w-]+\.js"', js)
    react = find_once("react namespace", rf"var (\w+)=\w+\({re.escape(react_import)}\(\)\)", js)
    jsx = find_once(
        "jsx-runtime use",
        r"=\(0,(\w+)\.jsx\)\(`div`,\{\"aria-hidden\":!0,className:`pointer-events-none fixed z-40`,", js)
    jsx_import = find_once("jsx-runtime namespace", rf"var {re.escape(jsx)}=(\w+)\(\)[;,]", js)
    find_once("jsx-runtime import",
              rf'import\{{[^}}]*\b\w+ as {re.escape(jsx_import)}[,}}][^;]*from"\./[\w-]+\.js"', js)
    dragging = find_once(
        "drag state",
        r"\{dragging:(\w+),dragAnimation:\w+,hovering:\w+,dragGeneration:\w+,handlers:\w+\}=\w+\(", js)
    bubble = BUBBLE.replace("__R__", react).replace("__J__", jsx)
    j = re.escape(jsx)

    def with_bubble(m):
        pos, size = find_once(
            "pet root style",
            rf"\b{re.escape(m['style'])}=\{{left:(\w+)\.x,top:\1\.y,width:(\w+),height:\2\}}", js)
        return (f"{m['head']}(0,{jsx}.jsxs)({jsx}.Fragment,{{children:[{m['root']},(0,{jsx}.jsx)(PetSpeechBubble,"
                f"{{x:{pos}.x,y:{pos}.y,size:{size},dragging:{dragging}}})]}})}}")

    js = sub_once(
        "pet root",
        rf"(?P<head>return \w+\[\d+\]!==\w+\|\|\w+\[\d+\]!==\w+\?\((?P<root>\w+)=\(0,{j}\.jsx\)\(`div`,"
        rf"\{{\"aria-hidden\":!0,className:`pointer-events-none fixed z-40`,style:(?P<style>\w+),"
        rf"children:\w+\}}\),[^{{}}]*?:(?P=root)=\w+\[\d+\],)(?P=root)\}}",
        with_bubble,
        js,
    )
    return sub_once("export", r"(export\{\w+ as PetOverlay,)", lambda m: f"{bubble}{m[1]}", js)


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

    packed, unpacked = [], []  # (path, node); packed sorted into archive order below
    def walk(node, prefix):
        for name, child in node["files"].items():
            p = f"{prefix}{name}"
            if "files" in child:
                walk(child, p + "/")
            elif child.get("unpacked"):
                unpacked.append((p, child))
            elif "offset" in child:
                packed.append((p, child))
    walk(header, "")
    packed.sort(key=lambda e: int(e[1]["offset"]))

    def patch(key, fn, node, data):
        data = fn(data.decode("utf-8")).encode("utf-8")
        node["integrity"] = integrity(data)
        node["size"] = len(data)
        patched.append(key)
        return data

    patched = []
    chunks, offset = [], 0
    for path, node in packed:
        start = base + int(node["offset"])
        data = raw[start:start + node["size"]]
        key, fn = target(path)
        if fn:
            data = patch(key, fn, node, data)
        node["offset"] = str(offset)
        node["size"] = len(data)
        chunks.append(data)
        offset += len(data)
    disk_writes = []
    for path, node in unpacked:
        key, fn = target(path)
        if fn:
            disk = os.path.join(asar_path + ".unpacked", path)
            with open(disk, "rb") as f:
                disk_writes.append((disk, patch(key, fn, node, f.read())))
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
    for disk, data in disk_writes:
        with open(disk, "wb") as f:
            f.write(data)
    print(f"{NAME}: patched {asar_path}")


if __name__ == "__main__":
    main(sys.argv[1])
