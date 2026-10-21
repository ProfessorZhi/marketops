"""Jev official Choice/Noul/Score payload contract — SDK mocked, paid calls zero."""
from __future__ import annotations
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import research_provider_extensions as extra


class FakeQuestion:
    def __init__(self, instructions="", criteria=None):
        self.instructions=instructions
        self.criteria=criteria

class Client:
    def __init__(self, **kwargs):
        self.options=kwargs
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def system_one(self,state,questions):
        assert "source_excerpt" in state
        x=next(iter(questions.values()))
        if isinstance(x, Choice):
            return types.SimpleNamespace(model="fake-jev",choices={
                "marketops_judgment":types.SimpleNamespace(choice="operator")})
        if isinstance(x, Noul):
            return types.SimpleNamespace(model="fake-jev",nouls={
                "marketops_judgment":types.SimpleNamespace(noul=0.25)})
        if isinstance(x, Score):
            return types.SimpleNamespace(model="fake-jev",scores={
                "marketops_judgment":types.SimpleNamespace(score=1.5)})
        raise AssertionError("unknown primitive")

class Choice(FakeQuestion): pass
class Noul(FakeQuestion): pass
class Score(FakeQuestion): pass
class RetryPolicy:
    def __init__(self,**kw): self.params=kw

class JevPrimitiveTests(unittest.TestCase):
    def setUp(self):
        mock=types.SimpleNamespace(Choice=Choice,Noul=Noul,Score=Score,
                                   TypeSafeClient=Client,RetryPolicy=RetryPolicy)
        patcher=patch.dict(sys.modules,{"typesafe_sdk":mock})
        patcher.start()
        self.addCleanup(patcher.stop)
        key=patch.dict(extra.os.environ,{"TYPESAFE_API_KEY":"fictional-test-key"})
        key.start()
        self.addCleanup(key.stop)
        self.doc="Official material: Operator of the candidate asset remains disputed."

    def test_choice_noul_score_all_wired_without_billable_request(self):
        cases=[
         ("choice",["operator","tenant"],"chosen_option","operator"),
         ("noul",[],"noul",0.25),
         ("score",["low support","some support","strong support"],"score",1.5)]
        for mode,criteria,field,expected in cases:
            with self.subTest(mode=mode):
                result=extra._jev_choice(document=self.doc,question="What does this source indicate?",
                                         options=criteria,primitive=mode)
                self.assertEqual(expected,result[field])
                self.assertTrue(result["judgment_only"])
                self.assertEqual(mode,result["primitive"])

    def test_validation_blocks_invalid_paid_requests(self):
        for mode,criteria in (("noul",["yes"]),("score",["one"]),("evil",[])):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    extra._jev_choice(document=self.doc,question="Does this evidence show operator?",
                                      options=criteria,primitive=mode)


if __name__=="__main__":
    unittest.main()
