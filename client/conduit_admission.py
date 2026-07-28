#!/usr/bin/env python3
"""Source-first Conduit admission client. No server-supplied code is executed."""
from __future__ import annotations
import argparse, asyncio, hashlib, json, os, re, stat, subprocess, sys, time, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HEADERS={"User-Agent":"conduit-client-admission/3","ngrok-skip-browser-warning":"1","Accept":"application/json"}
AUTH=Path("/home/user/.conduit_auth.json"); RESUME=Path("/home/user/.conduit_enrollment.json"); REPORT=Path("/home/user/.conduit_admission_report.json")

def fail(msg:str)->None: print(f"admission error: {msg}",file=sys.stderr); raise SystemExit(1)
def atomic_json(path:Path,data:dict[str,Any])->None:
 path.parent.mkdir(parents=True,exist_ok=True,mode=0o700); tmp=path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
 try:
  fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,"w",encoding="utf-8") as f: json.dump(data,f,indent=2); f.write("\n"); f.flush(); os.fsync(f.fileno())
  os.chmod(tmp,0o600); os.replace(tmp,path); os.chmod(path,0o600)
 finally: tmp.unlink(missing_ok=True)
def private_json(path:Path)->dict[str,Any]|None:
 if not path.exists(): return None
 i=path.stat()
 if not stat.S_ISREG(i.st_mode) or stat.S_IMODE(i.st_mode)!=0o600: fail(f"private state must be a regular mode-0600 file: {path}")
 if hasattr(os,"getuid") and i.st_uid!=os.getuid(): fail(f"private state is not current-user owned: {path}")
 d=json.loads(path.read_text());
 if not isinstance(d,dict): fail(f"invalid JSON object: {path}")
 return d
def base_url(url:str)->str:
 p=urllib.parse.urlsplit(url.strip().rstrip("/"));
 if p.scheme not in {"http","https"} or not p.netloc or p.username or p.password or p.query or p.fragment: fail("invalid server URL")
 path=p.path.rstrip("/"); path=path[:-4] if path.endswith("/mcp") else path
 return urllib.parse.urlunsplit((p.scheme,p.netloc,path,"",""))
def post(url:str,payload:dict[str,Any],timeout:int=30)->dict[str,Any]:
 req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={**HEADERS,"Content-Type":"application/json"},method="POST")
 with urllib.request.urlopen(req,timeout=timeout) as r: return json.loads(r.read().decode() or "{}")
def load_envelope(path:Path,allow_expired_resume:bool=False)->dict[str,Any]:
 d=json.loads(path.read_text()); required={"schemaVersion","provisioningId","purpose","lifecycle","authorization","server","client","requestedGrant","invite","credentialHandling"}
 if not isinstance(d,dict) or d.get("schemaVersion")!=3 or not required.issubset(d): fail("invalid provisioning schema v3 envelope")
 a=d["authorization"]
 if a.get("state")!="AUTHORIZED_TO_REQUEST_ENROLLMENT" or a.get("accessClass") not in {"LIVE_PROBATION","REGULAR_OPERATOR_PROMOTION"} or a.get("approvalRequired") is not True: fail("invalid authorization contract")
 exp=datetime.fromisoformat(str(a.get("expiresAt","")).replace("Z","+00:00"))
 if exp<=datetime.now(timezone.utc) and not allow_expired_resume: fail("provisioning envelope expired")
 if d["purpose"]=="PROMOTE_CLIENT" and not d.get("replacesClientId"): fail("promotion requires replacesClientId")
 g=d["requestedGrant"]
 if not isinstance(g,dict) or not g.get("privileges") or not g.get("workspaceIds"): fail("empty requested grant")
 h=d["credentialHandling"]
 expected={"resultingAuthPath":str(AUTH),"enrollmentResumePath":str(RESUME),"admissionReportPath":str(REPORT),"requiredMode":"0600"}
 if any(h.get(k)!=v for k,v in expected.items()): fail("unsupported credential-handling policy")
 return d
def verify_source(d:dict[str,Any])->dict[str,str]:
 root=Path(__file__).resolve().parents[1]; c=d["client"]
 if c.get("entrypoint")!="client/conduit_admission.py" or not re.fullmatch(r"[0-9a-f]{40}",str(c.get("commit",""))): fail("invalid client source pin")
 try:
  head=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(); remote=subprocess.check_output(["git","remote","get-url","origin"],cwd=root,text=True).strip()
 except Exception: fail("client must run from a Git clone")
 if head!=c["commit"]: fail(f"client commit mismatch: expected {c['commit']}, got {head}")
 def norm(x:str)->str: return x.removesuffix(".git").rstrip("/")
 if norm(remote)!=norm(c["repository"]): fail("client repository mismatch")
 return {"repository":c["repository"],"commit":head}
def summary(d:dict[str,Any],source:dict[str,str])->dict[str,Any]:
 return {"valid":True,"provisioningId":d["provisioningId"],"purpose":d["purpose"],"accessClass":d["authorization"]["accessClass"],"expiresAt":d["authorization"]["expiresAt"],"server":d["server"]["url"],"client":source,"requestedGrant":d["requestedGrant"],"networkUsed":False,"filesWritten":[]}
def enroll(d:dict[str,Any],timeout:int,poll:float)->tuple[str,str]:
 server=base_url(d["server"]["url"]); saved=private_json(RESUME)
 if saved:
  keys=("serverUrl","inviteId","provisioningId"); expected=(server,d["invite"]["id"],d["provisioningId"])
  if tuple(saved.get(k) for k in keys)!=expected: fail("resume state belongs to another provisioning envelope")
  rid,secret=saved["requestId"],saved["requestSecret"]
 else:
  created=post(server+"/admin/enroll/request",{"inviteId":d["invite"]["id"],"inviteSecret":d["invite"]["secret"],"label":os.getenv("CONDUIT_CLIENT_LABEL") or f"conduit-source:{uuid.uuid4()}","clientInfo":{"name":"conduit-client","version":"3-source","modelId":os.getenv("CONDUIT_AGENT_MODEL") or "undisclosed"},"requestedGrant":d["requestedGrant"]},max(30,timeout))
  rid,secret=created.get("requestId"),created.get("requestSecret")
  if not rid or not secret: fail("invalid enrollment response")
  atomic_json(RESUME,{"schemaVersion":1,"serverUrl":server,"inviteId":d["invite"]["id"],"provisioningId":d["provisioningId"],"requestId":rid,"requestSecret":secret,"savedAt":time.time()})
 print(json.dumps({"requestId":rid,"status":"pending"}))
 deadline=time.monotonic()+timeout
 while time.monotonic()<deadline:
  status=post(server+"/admin/enroll/status",{"requestId":rid,"requestSecret":secret},max(30,timeout)); state=status.get("status")
  if state=="approved" and status.get("clientToken"): return str(status["clientToken"]),str(status.get("clientId", ""))
  if state=="denied": RESUME.unlink(missing_ok=True); fail("enrollment denied")
  if state!="pending": fail("unexpected enrollment status")
  time.sleep(max(.25,poll))
 fail("approval timeout; resume state retained, rerun the same command")
async def verify(token:str,d:dict[str,Any])->dict[str,Any]:
 from client.conduit import Conduit
 grant=d["requestedGrant"]; default=grant.get("defaultWorkspaceId") or grant["workspaceIds"][0]
 async with Conduit(base_url(d["server"]["url"]),workspace=default,client_token=token) as c:
  visible=await c.workspace.list(); current=await c.workspace.current(); health=await c.system.health()
 ids=sorted(str(x.get("id")) for x in visible)
 if ids!=sorted(grant["workspaceIds"]): fail(f"actual workspace grant mismatch: {ids}")
 return {"workspaceIdsGranted":ids,"defaultWorkspace":current,"runtimeHealthy":health.get("status")=="healthy"}
def main()->int:
 ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest="cmd",required=True)
 for name in ["inspect","enroll"]:
  p=sub.add_parser(name); p.add_argument("--provisioning",required=True); p.add_argument("--timeout",type=int,default=900); p.add_argument("--poll",type=float,default=5)
 args=ap.parse_args(); path=Path(args.provisioning); d=load_envelope(path,allow_expired_resume=args.cmd=="enroll" and RESUME.exists()); source=verify_source(d)
 if args.cmd=="inspect": print(json.dumps(summary(d,source),indent=2)); return 0
 token,cid=enroll(d,args.timeout,args.poll); actual=asyncio.run(verify(token,d)); atomic_json(AUTH,{"schemaVersion":1,"clientToken":token,"serverUrl":base_url(d["server"]["url"]),"savedAt":time.time()}); RESUME.unlink(missing_ok=True)
 report={"schemaVersion":1,"outcome":"ENROLLED","provisioningId":d["provisioningId"],"purpose":d["purpose"],"accessClass":d["authorization"]["accessClass"],"clientId":cid,**source,"requestedGrant":d["requestedGrant"],**actual,"authPath":str(AUTH),"authMode":"0600","resumeStatePresent":False,"secretsPrinted":False,"completedAt":datetime.now(timezone.utc).isoformat()}
 atomic_json(REPORT,report); print(json.dumps(report,indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
