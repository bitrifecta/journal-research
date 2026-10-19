"""Offline logic checks (no network): DOI normalization, dedup, OpenAlex abstract reassembly."""
import server as s

assert s._norm_doi("https://doi.org/10.1234/ABC.xyz") == "10.1234/abc.xyz"
assert s._norm_doi("10.1234/x") == "10.1234/x"
assert s._norm_doi("not-a-doi") == ""
assert s._norm_doi(None) == ""

inv = {"Reinforcement": [0], "learning": [1], "for": [2], "robots": [3]}
assert s._oa_abstract(inv) == "Reinforcement learning for robots"
assert s._oa_abstract(None) == ""

b1 = {"title": "deep  RL!", "doi": ""}
b2 = {"title": "Deep RL", "doi": ""}
c = {"title": "Other", "doi": ""}
assert s._key(b1) == s._key(b2) and s._key(b1) != s._key(c)
assert s._key({"title": "x", "doi": ""}) == "t:x"
assert s._key({"title": "", "doi": ""}) == "t:"

print("check ok")
