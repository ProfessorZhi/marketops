"""All official Google-category Lite search modes are still discovery only."""
from __future__ import annotations
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"scripts"))
import dual_search_mcp as dual


class SearchEndpointTests(unittest.TestCase):
    def test_serper_endpoints_are_allowlisted_and_one_provider_only(self):
        with patch.dict(os.environ,{"SERPER_API_KEY":"fake-key"}):
            for mode,endpoint in (("web","search"),("maps","places"),
                                  ("images","images"),("news","news"),
                                  ("videos","videos"),("shopping","shopping"),
                                  ("scholar","scholar"),("patents","patents"),
                                  ("autocomplete","autocomplete")):
                with self.subTest(mode=mode):
                    observed=[]
                    class Response:
                        status=200
                        def __enter__(self): return self
                        def __exit__(self,*a): pass
                        def read(self,n): return b'{"organic":[]}'
                    def fake(req,timeout):
                        observed.append(req)
                        return Response()
                    dual._request("serper",mode,"a business","SG","en",4,opener=fake)
                    self.assertEqual("https://google.serper.dev/"+endpoint,
                                     observed[0].full_url)
                    self.assertEqual("POST",observed[0].get_method())
                    self.assertEqual("a business",json.loads(observed[0].data)["q"])

    def test_serpapi_specialized_engines_are_explicit(self):
        with patch.dict(os.environ,{"SERPAPI_API_KEY":"fake-key"}):
            for mode,engine in (("web","google"),("maps","google_maps"),
                                ("news","google_news"),("shopping","google_shopping"),
                                ("scholar","google_scholar"),("images","google_images")):
                with self.subTest(mode=mode):
                    urls=[]
                    class Response:
                        status=200
                        def __enter__(self): return self
                        def __exit__(self,*a): pass
                        def read(self,n): return b'{"organic_results":[]}'
                    def fake(req,timeout):
                        urls.append(req.full_url)
                        return Response()
                    dual._request("serpapi",mode,"target","SG","en",3,opener=fake)
                    self.assertIn("engine="+engine,urls[0])
                    self.assertIn("serpapi.com/search.json?",urls[0])

    def test_result_types_are_discovery_candidates_only(self):
        for provider,mode,data in (
             ("serper","news",{"news":[{"title":"Public Announcement",
                        "link":"https://official.example.org/news","snippet":"News"}]}),
             ("serper","shopping",{"shopping":[{"title":"Target Product",
                        "link":"https://store.example.org/p"}]}),
             ("serpapi","images",{"images_results":[{"title":"Building photo",
                        "link":"https://official.example.org/photo"}]}),
             ("serper","autocomplete",{"suggestions":["target company"]}),
        ):
            with self.subTest(provider=provider,mode=mode):
                result=dual._normalize(provider,mode,data,4)
                self.assertEqual(1,len(result))
                self.assertEqual("discovered",result[0]["status"])
                self.assertEqual(mode,result[0]["search_mode"])
                self.assertFalse("verified" in result[0]["kind"])
        with self.assertRaisesRegex(ValueError,"UNSUPPORTED_SEARCH_MODE"):
            with patch.dict(os.environ,{"MARKETOPS_SEARCH_ALLOW_NETWORK":"1",
                                      "SERPER_API_KEY":"fake-key",
                                      "MARKETOPS_SEARCH_MAX_CALLS":"1"}):
                dual._check_args("serper","query","SG","en","run","op","scrape_everything",5)


if __name__=="__main__":
    unittest.main()
