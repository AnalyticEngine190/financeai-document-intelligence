"""Independent multi-document benchmark for /ask_v2.

Reference values and document+page pairs must be human-verified BEFORE the
first run. The generator deliberately leaves them blank; no fabricated gold.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import urllib.error
import urllib.request

DEFAULT_API = "http://127.0.0.1:8000"
CATEGORIES = (("nordea_q2",10),("new_bank",12),("isolation",8),
              ("cross_document",6),("unsupported_ambiguous",4))


def create_template(path: Path) -> None:
    cases = []
    for category, count in CATEGORIES:
        for i in range(1,count+1):
            cases.append({"id":f"{category}_{i:02d}", "category":category,
                          "question":"", "document_selection":{"mode":"manual","document_ids":[]},
                          "expected_outcome":None, "expected_value":None,
                          "expected_unit":None, "expected_sources":[]})
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({"dataset_version":"Day10-template", "cases":cases},indent=2),encoding="utf-8")
    print(f"Created {path}. Fill questions, selection, outcomes, source pairs and values from PDFs before testing.")


def validate(data: dict) -> list[dict]:
    cases = data.get("cases")
    if not isinstance(cases,list) or not cases:
        raise ValueError("Dataset must contain a nonempty 'cases' list")
    seen=set()
    for c in cases:
        if not isinstance(c,dict):
            raise ValueError("Every case must be a JSON object")
        cid=c.get("id")
        if not cid or cid in seen:
            raise ValueError("Case IDs must be nonempty and distinct")
        seen.add(cid)
        if not isinstance(c.get("question"),str) or not c["question"].strip():
            raise ValueError(f"{cid}: question is blank")
        if c.get("expected_outcome") not in ("answer","abstain","clarify"):
            raise ValueError(f"{cid}: expected_outcome must be answer, abstain, or clarify")
        selection=c.get("document_selection")
        if not isinstance(selection,dict) or selection.get("mode") not in ("auto","manual","compare"):
            raise ValueError(f"{cid}: document_selection.mode invalid")
        ids=selection.get("document_ids")
        if not isinstance(ids,list) or (selection["mode"]=="manual" and len(ids)!=1) or (selection["mode"]=="compare" and len(ids)!=2):
            raise ValueError(f"{cid}: invalid selected document IDs")
        if c["expected_outcome"] == "answer":
            if c.get("expected_value") in (None,""):
                raise ValueError(f"{cid}: missing reference numerical answer")
            try:
                Decimal(str(c["expected_value"]).replace(",",""))
            except InvalidOperation:
                raise ValueError(f"{cid}: expected_value must be numeric")
            if not c.get("expected_sources"):
                raise ValueError(f"{cid}: supported answer needs exact document/page reference")
        for s in c.get("expected_sources",[]):
            if not isinstance(s,dict) or not s.get("document_id") or not isinstance(s.get("page"),int) or s["page"]<1:
                raise ValueError(f"{cid}: reference citations require document_id and physical PDF page")
    return cases


def first_number(answer: str) -> Decimal | None:
    # The Day 10 API begins structured answers with their result, followed by
    # operand explanation. Do not let the operand 'correct' a wrong result.
    match = re.search(r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w])",answer[:90])
    try:
        return Decimal(match.group().replace(",","")) if match else None
    except InvalidOperation:
        return None


def grade(case: dict, response: dict) -> tuple[bool,str]:
    outcome=case["expected_outcome"]
    cited = {(s.get("document_id"),s.get("page")) for s in response.get("sources",[]) if isinstance(s,dict)}
    if outcome=="abstain":
        ok=(response.get("supported") is False and not response.get("needs_clarification") and not cited)
        return ok,"" if ok else "not_clean_abstention"
    if outcome=="clarify":
        ok=(response.get("needs_clarification") is True and response.get("supported") is False and not cited)
        return ok,"" if ok else "did_not_clarify"
    if response.get("supported") is not True:
        return False,"unsupported"
    expected=Decimal(str(case["expected_value"]).replace(",",""))
    if first_number(str(response.get("answer","")))!=expected:
        return False,"wrong_primary_number"
    expected_sources={(s["document_id"],s["page"]) for s in case["expected_sources"]}
    if cited!=expected_sources:
        return False,"wrong_report_or_page"
    unit=case.get("expected_unit")
    a=str(response.get("answer","")).casefold()
    if unit=="percent" and "%" not in a[:90]:
        return False,"missing_percent_unit"
    if unit=="percentage_points" and "percentage points" not in a[:90]:
        return False,"missing_percentage_points_unit"
    if unit and re.fullmatch(r"[A-Z]{3}(?:m|bn)",unit):
        name="million" if unit.endswith("m") else "billion"
        if unit[:3].casefold() not in a[:90] or name not in a[:90]:
            return False,"missing_currency_unit"
    return True,""


def _post(url: str, payload: dict) -> dict:
    req=urllib.request.Request(url.rstrip("/")+"/ask_v2",
        data=json.dumps(payload).encode(),headers={"Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=200) as response:
        return json.load(response)


def main() -> None:
    p=argparse.ArgumentParser(description="Day 10 source-aware independent evaluator")
    p.add_argument("--make-template",type=Path)
    p.add_argument("--dataset",type=Path)
    p.add_argument("--validate-only",action="store_true")
    p.add_argument("--api-url",default=DEFAULT_API)
    args=p.parse_args()
    if args.make_template:
        create_template(args.make_template)
        return
    if not args.dataset:
        p.error("Provide --dataset, or generate one with --make-template")
    cases=validate(json.loads(args.dataset.read_text(encoding="utf-8")))
    print(f"Dataset valid: {len(cases)} human-verified reference cases")
    if args.validate_only:
        return
    outputs=[]
    for index,case in enumerate(cases,1):
        try:
            response=_post(args.api_url,{"question":case["question"],
                       "document_selection":case["document_selection"]})
            passed,issue=grade(case,response)
        except (urllib.error.URLError,TimeoutError,ValueError) as exc:
            response={"answer":"", "sources":[]}; passed=False; issue="transport_error:"+str(exc)
        row={"id":case["id"],"category":case.get("category",""),"passed":passed,
             "issue":issue,"question":case["question"],"answer":response.get("answer",""),
             "sources":response.get("sources",[]),"expected_value":case.get("expected_value")}
        outputs.append(row)
        print(f"[{index:02d}/{len(cases):02d}] {case['id']} {'PASS' if passed else 'FAIL'} {issue}")
    passed=sum(r["passed"] for r in outputs)
    now=datetime.now().strftime("%Y%m%d_%H%M%S")
    out=Path("reports/evaluation")
    out.mkdir(parents=True,exist_ok=True)
    target=out/f"day10_{now}.json"
    target.write_text(json.dumps({"summary":{"passed":passed,"total":len(outputs)},"results":outputs},indent=2),encoding="utf-8")
    with (out/f"day10_{now}.csv").open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=["id","category","passed","issue","question","answer","sources","expected_value"])
        writer.writeheader(); writer.writerows(outputs)
    print(f"STRICT: {passed}/{len(outputs)} ({100*passed/len(outputs):.1f}%)\nSaved: {target}")


if __name__=="__main__":
    main()
