"""Routes of Decide mode, batch approve, inbox triage and delegations (DESK-0285). Registered by create_app with one line.

Only the store owner, as the request's own identity, decides here (decision 110): an owner confirmed by the OS user, or named by this
browser or assumed because nobody is linked on this machine yet (decidemode.may_decide, checked again by decidemode.record). Anyone else sees the same screens read-only, with a sentence saying why. Every write runs inside
`activity.batch(<id>)` and redirects with `undo=<id>&msg=<sentence>` (the DESK-0284 toast contract). Docs: docs/COMMANDS.md."""

from __future__ import annotations

import datetime as dt
import html
import re
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import activity, decidemode as dm, delegations as dg, lanes, people as peoplemod, triage as tri
from ..rules import RuleError

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


def plain(text: str, limit: int = 280) -> str:
    """A short plain excerpt of a section for a tile: markdown marks, comments and links dropped, cut at a word."""
    text = re.sub(r"<!--.*?-->", " ", text or "", flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s*(?:[-*+]|\d+[.)]|>|#+)\s*", "", text, flags=re.M)
    text = " ".join(text.replace("**", "").replace("`", "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _find(items, tid: str):
    for i in items:
        if i.id == tid:
            return i
    return None


def build(*, templates, ctx, store, tokens, acting) -> APIRouter:
    r = APIRouter()
    templates.env.globals["delegations"] = lambda: dg.load(store().home)

    def may_decide(request, s) -> tuple[bool, str]:
        return dm.may_decide(acting(request), s.owner)  # the same rule decidemode.record checks again on every write

    def spent() -> HTMLResponse:
        return HTMLResponse("<!doctype html><meta charset=utf-8><title>Already sent</title><body style=\"font:16px system-ui;max-width:40em;margin:3em auto;padding:0 1em\">"
                            "<h1>That form was already sent</h1><p>This button works once, so a double click can not post twice. Nothing more was written.</p>"
                            "<p><a href=\"/decide\">Back to Decide</a></p></body>", status_code=409)

    def refuse(msg: str, back: str = "/decide") -> HTMLResponse:
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Not allowed</title><body style=\"font:16px system-ui;max-width:40em;margin:3em auto;padding:0 1em\">"
                            f"<h1>Nothing was recorded</h1><p>{html.escape(msg)}</p><p><a href=\"{html.escape(back)}\">Back</a></p></body>", status_code=403)

    def go(path: str, **params) -> RedirectResponse:
        q = urlencode({k: v for k, v in params.items() if v})
        return RedirectResponse(path + ("?" + q if q else ""), status_code=303)

    # ---- Decide mode ----------------------------------------------------------------------------------------------------------

    @r.get("/decide", response_class=HTMLResponse)
    def decide_page(request: Request, at: str = "", msg: str = "", error: str = "", undo: str = ""):
        s = store()
        now = dt.datetime.now()
        items = dm.queue(s, now)
        can, why_not = may_decide(request, s)
        pos = next((n for n, i in enumerate(items) if i.id == at.upper()), 0) if items else 0
        view = None
        if items:
            it = items[pos]
            t = it.ticket
            ask = dm.ask_for(t, it.kind)
            rec = dm.recommendation(t, ask)
            pe = peoplemod.load(s.home)
            view = {"item": it, "t": t, "ask": ask, "rec": rec, "steps": dm.next_steps(s, t, it.kind, now.date()), "earlier": dm.similar_answer(s, t),
                    "about": plain(t.section("Report") if t.status == "review" and t.section("Report") else t.section("Context")),
                    "prev": items[pos - 1].id if pos > 0 else "", "next": items[pos + 1].id if pos + 1 < len(items) else "",
                    "delegates": [(p.key, p.name) for p in pe.list if p.key.lower() != s.owner.lower() and p.kind in ("agent", "coworker", "code", "person")],
                    "pattern": re.sub(r"^\s*(approve|choose|answer|provide)\s*:\s*", "", str(t.meta.get("title") or ""), flags=re.I)[:120]}
        return templates.TemplateResponse(request, "decide.html", ctx(request, s, items=items, pos=pos, v=view, can=can, why_not=why_not, msg=msg, error=error,
                                                                       agree=dm.AGREE, no_rec=dm.NO_REC))

    @r.post("/decide/answer")
    def decide_answer(request: Request, id: str = Form(""), action: str = Form(""), option: str = Form(""), words: str = Form(""), next: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return spent()
        s = store()
        can, why_not = may_decide(request, s)
        if not can:
            return refuse(why_not)
        nxt = next.upper() if _ID.match(next or "") else ""
        try:
            t = s.get(id)
            item = _find(dm.queue(s), t.id)
            if item is None:
                raise ValueError(f"{t.id} is not waiting on you any more. Nothing was recorded.")
            bid = dm.new_batch_id()
            if action == "snooze":
                s.sync_before()
                with activity.batch(bid):
                    until = dm.snooze(s, t)
                s.sync_after(f"desk: snooze {t.id}")
                return go("/decide", at=nxt, undo=bid, msg=f"{t.id} snoozed until {until:%a %b} {until.day}.")
            if action not in ("approve", "changes"):
                raise ValueError("Press Y to agree, N to send back or S to snooze.")
            ask = dm.ask_for(t, item.kind)
            text = dm.words(item.kind, ask, dm.recommendation(t, ask), option, words, action)
            with activity.batch(bid):
                dm.record(s, t, action == "approve", text, viewer=acting(request))
            done = "sent back" if action == "changes" else "decided"
            return go("/decide", at=nxt, undo=bid, msg=f"{t.id} {done}: “{text}”")
        except (KeyError, RuleError, ValueError) as e:
            return go("/decide", at=id.upper(), error=e.args[0] if isinstance(e, KeyError) and e.args else str(e))

    @r.post("/decide/delegate")
    def decide_delegate(request: Request, id: str = Form(""), who: str = Form(""), pattern: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return spent()
        s = store()
        can, why_not = may_decide(request, s)
        if not can:
            return refuse(why_not)
        try:
            t = s.get(id)
            row = dg.add(s.home, who, pattern, by=s.owner, project=t.project, source=t.id)
        except (KeyError, ValueError) as e:
            return go("/decide", at=id.upper(), error=e.args[0] if isinstance(e, KeyError) and e.args else str(e))
        name = peoplemod.load(s.home).display(row["who"]) if peoplemod.load(s.home).get(row["who"]) else row["who"]
        return go("/decide", at=id.upper(), msg=f"Saved: {name} may approve “{row['pattern']}” for you. Remove it on Settings.")

    # ---- batch approve from Needs me ------------------------------------------------------------------------------------------

    @r.get("/decide/batch", response_class=HTMLResponse)
    def batch_page(request: Request, ids: list[str] = Query([]), back: str = "/"):
        s = store()
        can, why_not = may_decide(request, s)
        plan = dm.plan_batch(s, ids)
        safe_back = back if re.fullmatch(r"/[A-Za-z0-9_?=&%.\-/]*", back or "") else "/"
        return templates.TemplateResponse(request, "decide_batch.html", ctx(request, s, plan=plan, ok=[p for p in plan if not p.skip], left=[p for p in plan if p.skip],
                                                                             can=can, why_not=why_not, back=safe_back))

    @r.post("/decide/batch")
    def batch_post(request: Request, ids: list[str] = Form([]), back: str = Form("/"), token: str = Form("")):
        if not tokens.use(token):
            return spent()
        s = store()
        can, why_not = may_decide(request, s)
        if not can:
            return refuse(why_not, "/")
        safe_back = back if re.fullmatch(r"/[A-Za-z0-9_?=&%.\-/]*", back or "") and "?" not in back else "/"
        plan = dm.plan_batch(s, ids)
        bid = dm.new_batch_id()
        done, refused = dm.run_batch(s, plan, bid, viewer=acting(request))
        if not done:
            return go(safe_back, msg="Nothing was approved: none of those can be approved in a batch. Open them one by one.")
        msg = f"Approved {len(done)} ticket{'s' if len(done) != 1 else ''}: {', '.join(done)}."
        if refused:
            msg += f" Not approved: {', '.join(i for i, _ in refused)}."
        return go(safe_back, undo=bid, msg=msg)

    # ---- inbox triage ---------------------------------------------------------------------------------------------------------

    @r.get("/triage", response_class=HTMLResponse)
    def triage_page(request: Request, at: str = "", msg: str = "", error: str = "", undo: str = ""):
        s = store()
        items = tri.queue(s)
        can, why_not = may_decide(request, s)
        pos = next((n for n, t in enumerate(items) if t.id == at.upper()), 0) if items else 0
        t = items[pos] if items else None
        return templates.TemplateResponse(request, "triage.html", ctx(request, s, items=items, pos=pos, t=t, can=can, why_not=why_not, msg=msg, error=error,
                                                                       choices=tri.CHOICES, about=plain(t.section("Context")) if t else "",
                                                                       outcome=plain(t.section("Outcome"), 200) if t else "",
                                                                       lane_now=lanes.lane_of(t) if t else "",
                                                                       prev=items[pos - 1].id if items and pos > 0 else "",
                                                                       next_id=items[pos + 1].id if items and pos + 1 < len(items) else ""))

    @r.post("/triage/place")
    def triage_place(request: Request, id: str = Form(""), lane: str = Form(""), reason: str = Form(""), into: str = Form(""), next: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return spent()
        s = store()
        can, why_not = may_decide(request, s)
        if not can:
            return refuse(why_not, "/triage")
        nxt = next.upper() if _ID.match(next or "") else ""
        bid = dm.new_batch_id()
        try:
            t = s.get(id)
            s.sync_before()
            with activity.batch(bid):
                if lane == "merge":
                    tri.merge(s, t, into, actor=s.owner)
                    msg = f"{t.id} merged into {into.strip().upper()}."
                else:
                    tri.place(s, t, lane, actor=s.owner, reason=reason)
                    msg = f"{t.id} is in {tri.LABEL[lane]}."
            s.sync_after(f"desk: triage {t.id}")
        except (KeyError, RuleError, ValueError) as e:
            return go("/triage", at=id.upper(), error=e.args[0] if isinstance(e, KeyError) and e.args else str(e))
        return go("/triage", at=nxt, undo=bid, msg=msg)

    # ---- delegations on Settings ----------------------------------------------------------------------------------------------

    @r.post("/settings/delegations/remove")
    def delegation_remove(request: Request, index: int = Form(-1), pattern: str = Form(""), token: str = Form("")):
        if not tokens.use(token):
            return spent()
        s = store()
        can, why_not = may_decide(request, s)
        if not can:
            return refuse(why_not, "/settings")
        try:
            gone = dg.remove(s.home, index, pattern)
        except ValueError as e:
            return RedirectResponse(f"/settings?msg={quote(str(e))}#delegations", status_code=303)
        return RedirectResponse(f"/settings?msg={quote('Removed: ' + gone['pattern'])}#delegations", status_code=303)

    return r
