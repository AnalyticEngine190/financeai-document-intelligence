import pytest
from scripts.evaluate_day10 import first_number,grade,create_template,validate
import json

def _case(value="20",mode="manual"):
    return {"id":"T1","question":"What was the difference?",
        "document_selection":{"mode":mode,"document_ids":["abc"]},
        "expected_outcome":"answer","expected_value":value,"expected_unit":"EURm",
        "expected_sources":[{"document_id":"abc","page":7}]}

def test_template_requires_references_before_run(tmp_path):
    p=tmp_path/'template.json'
    create_template(p)
    data=json.loads(p.read_text())
    assert len(data['cases'])==40
    with pytest.raises(ValueError):
        validate(data)

def test_primary_result_not_operand_is_graded():
    case=_case()
    response={"supported":True,"answer":"EUR 1,779 million. Difference: EUR 20 million.",
              "sources":[{"document_id":"abc","page":7}]}
    assert grade(case,response)[1]=="wrong_primary_number"

def test_wrong_document_citation_fails_even_on_correct_value():
    case=_case()
    response={"supported":True,"answer":"EUR 20 million", "sources":[{"document_id":"different","page":7}]}
    assert grade(case,response)[1]=="wrong_report_or_page"

def test_correct_value_document_and_unit_pass():
    case=_case()
    response={"supported":True,"answer":"EUR 20 million", "sources":[{"document_id":"abc","page":7}]}
    assert grade(case,response)==(True,"")
