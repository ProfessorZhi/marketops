#!/usr/bin/env python3
"""Country and language as the search vendors want them: "France" -> "FR", "French" -> "fr".

Agents write what the user wrote. In the Paris field run, ``country="France"``
failed every Serper search (36 of them) on the two-letter check, and the merged
search reported only "IDENTITY_VALUE_INVALID", so nobody saw why. Names are turned
into codes here, at the tool boundary; the ledger identity stays strict.
An unknown name is passed through unchanged, so the strict check still names it.
"""
from __future__ import annotations

__all__ = ["country_code", "language_code"]

_COUNTRIES = {
    "US": ("united states", "united states of america", "usa", "america", "美国"),
    "GB": ("united kingdom", "uk", "great britain", "britain", "england", "scotland", "wales",
           "英国"),
    "IE": ("ireland", "爱尔兰"),
    "FR": ("france", "法国"),
    "DE": ("germany", "deutschland", "德国"),
    "ES": ("spain", "españa", "espana", "西班牙"),
    "IT": ("italy", "italia", "意大利"),
    "PT": ("portugal", "葡萄牙"),
    "NL": ("netherlands", "the netherlands", "holland", "荷兰"),
    "BE": ("belgium", "比利时"),
    "LU": ("luxembourg", "卢森堡"),
    "CH": ("switzerland", "瑞士"),
    "AT": ("austria", "奥地利"),
    "DK": ("denmark", "丹麦"),
    "SE": ("sweden", "瑞典"),
    "NO": ("norway", "挪威"),
    "FI": ("finland", "芬兰"),
    "PL": ("poland", "波兰"),
    "CZ": ("czech republic", "czechia", "捷克"),
    "GR": ("greece", "希腊"),
    "TR": ("turkey", "türkiye", "turkiye", "土耳其"),
    "CA": ("canada", "加拿大"),
    "MX": ("mexico", "墨西哥"),
    "BR": ("brazil", "巴西"),
    "AR": ("argentina", "阿根廷"),
    "AU": ("australia", "澳大利亚", "澳洲"),
    "NZ": ("new zealand", "新西兰"),
    "JP": ("japan", "日本"),
    "KR": ("south korea", "korea", "韩国"),
    "CN": ("china", "中国"),
    "HK": ("hong kong", "香港"),
    "TW": ("taiwan", "台湾"),
    "SG": ("singapore", "新加坡"),
    "MY": ("malaysia", "马来西亚"),
    "TH": ("thailand", "泰国"),
    "VN": ("vietnam", "viet nam", "越南"),
    "ID": ("indonesia", "印度尼西亚", "印尼"),
    "PH": ("philippines", "菲律宾"),
    "IN": ("india", "印度"),
    "AE": ("united arab emirates", "uae", "阿联酋"),
    "SA": ("saudi arabia", "沙特", "沙特阿拉伯"),
    "IL": ("israel", "以色列"),
    "ZA": ("south africa", "南非"),
    "EG": ("egypt", "埃及"),
}
_LANGUAGES = {
    "en": ("english", "英语", "英文"), "fr": ("french", "français", "francais", "法语", "法文"),
    "de": ("german", "deutsch", "德语"), "es": ("spanish", "español", "espanol", "西班牙语"),
    "it": ("italian", "italiano", "意大利语"), "pt": ("portuguese", "português", "葡萄牙语"),
    "nl": ("dutch", "nederlands", "荷兰语"), "sv": ("swedish", "瑞典语"),
    "da": ("danish", "丹麦语"), "no": ("norwegian", "挪威语"), "fi": ("finnish", "芬兰语"),
    "pl": ("polish", "波兰语"), "el": ("greek", "希腊语"), "tr": ("turkish", "土耳其语"),
    "ja": ("japanese", "日语"), "ko": ("korean", "韩语"), "zh": ("chinese", "中文", "汉语"),
    "ar": ("arabic", "阿拉伯语"), "ru": ("russian", "俄语"), "hi": ("hindi", "印地语"),
}
_BY_COUNTRY_NAME = {name: code for code, names in _COUNTRIES.items() for name in names}
_BY_LANGUAGE_NAME = {name: code for code, names in _LANGUAGES.items() for name in names}


def country_code(value):
    """A two-letter code for a country name; anything else unchanged."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) == 2 and text.isascii() and text.isalpha():
        return text.upper()
    return _BY_COUNTRY_NAME.get(text.casefold(), value)


def language_code(value):
    """A language code for a language name; anything else unchanged."""
    if not isinstance(value, str):
        return value
    return _BY_LANGUAGE_NAME.get(value.strip().casefold(), value)
