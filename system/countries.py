"""Country names, ISO codes and nationality words mapped to one canonical name.

Territories keep their own name (policy: territories count as their own country).
Anything not listed passes through as its cleaned title-case form, so two sides
that write the same unknown name still compare equal.
"""

import re
import unicodedata

# canonical name: [aliases: ISO2 code, other spellings, nationality words]
_TABLE = {
    "Afghanistan": ["AF", "Afghan"],
    "Albania": ["AL", "Albanian"],
    "Algeria": ["DZ", "Algerian"],
    "Andorra": ["AD", "Andorran"],
    "Angola": ["AO", "Angolan"],
    "Argentina": ["AR", "Argentine", "Argentinian"],
    "Armenia": ["AM", "Armenian"],
    "Australia": ["AU", "Australian"],
    "Austria": ["AT", "Austrian"],
    "Azerbaijan": ["AZ", "Azerbaijani"],
    "Bahamas": ["BS", "Bahamian", "The Bahamas"],
    "Bahrain": ["BH", "Bahraini"],
    "Bangladesh": ["BD", "Bangladeshi"],
    "Barbados": ["BB", "Barbadian"],
    "Belarus": ["BY", "Belarusian"],
    "Belgium": ["BE", "Belgian"],
    "Belize": ["BZ", "Belizean"],
    "Benin": ["BJ", "Beninese"],
    "Bolivia": ["BO", "Bolivian", "Bolivia, Plurinational State of"],
    "Bosnia and Herzegovina": ["BA", "Bosnian", "Bosnia"],
    "Botswana": ["BW", "Motswana", "Batswana"],
    "Brazil": ["BR", "Brazilian"],
    "Brunei": ["BN", "Bruneian", "Brunei Darussalam"],
    "Bulgaria": ["BG", "Bulgarian"],
    "Burkina Faso": ["BF", "Burkinabe"],
    "Cambodia": ["KH", "Cambodian"],
    "Cameroon": ["CM", "Cameroonian"],
    "Canada": ["CA", "Canadian"],
    "Chile": ["CL", "Chilean"],
    "China": ["CN", "Chinese", "People's Republic of China", "PRC"],
    "Colombia": ["CO", "Colombian"],
    "Costa Rica": ["CR", "Costa Rican"],
    "Cote D'Ivoire": ["CI", "Ivorian", "Ivory Coast", "Côte d'Ivoire", "Cote d'Ivoire"],
    "Croatia": ["HR", "Croatian"],
    "Cuba": ["CU", "Cuban"],
    "Cyprus": ["CY", "Cypriot"],
    "Czech Republic": ["CZ", "Czech", "Czechia"],
    "Democratic Republic of the Congo": ["CD", "Congo, The Democratic Republic of the", "DR Congo", "DRC"],
    "Congo": ["CG", "Republic of the Congo", "Congolese"],
    "Denmark": ["DK", "Danish"],
    "Dominican Republic": ["DO", "Dominican"],
    "Ecuador": ["EC", "Ecuadorian", "Ecuadorean"],
    "Egypt": ["EG", "Egyptian"],
    "El Salvador": ["SV", "Salvadoran", "Salvadorean"],
    "Estonia": ["EE", "Estonian"],
    "Ethiopia": ["ET", "Ethiopian"],
    "Finland": ["FI", "Finnish"],
    "France": ["FR", "French"],
    "Gabon": ["GA", "Gabonese"],
    "Gambia": ["GM", "Gambian", "The Gambia"],
    "Georgia": ["GE", "Georgian"],
    "Germany": ["DE", "German"],
    "Ghana": ["GH", "Ghanaian"],
    "Greece": ["GR", "Greek"],
    "Guatemala": ["GT", "Guatemalan"],
    "Guinea": ["GN", "Guinean"],
    "Guyana": ["GY", "Guyanese"],
    "Haiti": ["HT", "Haitian"],
    "Honduras": ["HN", "Honduran"],
    "Hong Kong": ["HK", "Hong Kong SAR", "Hong Kong Special Administrative Region of China", "Hong Kong, China"],
    "Hungary": ["HU", "Hungarian"],
    "Iceland": ["IS", "Icelandic", "Icelander"],
    "India": ["IN", "Indian"],
    "Indonesia": ["ID", "Indonesian"],
    "Iran": ["IR", "Iranian", "Iran, Islamic Republic of", "Islamic Republic of Iran"],
    "Iraq": ["IQ", "Iraqi"],
    "Ireland": ["IE", "Irish", "Republic of Ireland"],
    "Israel": ["IL", "Israeli"],
    "Italy": ["IT", "Italian"],
    "Jamaica": ["JM", "Jamaican"],
    "Japan": ["JP", "Japanese"],
    "Jordan": ["JO", "Jordanian"],
    "Kazakhstan": ["KZ", "Kazakh", "Kazakhstani"],
    "Kenya": ["KE", "Kenyan"],
    "Kosovo": ["XK", "Kosovar"],
    "Kuwait": ["KW", "Kuwaiti"],
    "Kyrgyzstan": ["KG", "Kyrgyz"],
    "Laos": ["LA", "Lao", "Laotian", "Lao People's Democratic Republic"],
    "Latvia": ["LV", "Latvian"],
    "Lebanon": ["LB", "Lebanese"],
    "Liberia": ["LR", "Liberian"],
    "Libya": ["LY", "Libyan"],
    "Liechtenstein": ["LI"],
    "Lithuania": ["LT", "Lithuanian"],
    "Luxembourg": ["LU", "Luxembourgish", "Luxembourger"],
    "Macao": ["MO", "Macau", "Macao Special Administrative Region of China", "Macao SAR", "Macau SAR"],
    "Madagascar": ["MG", "Malagasy"],
    "Malawi": ["MW", "Malawian"],
    "Malaysia": ["MY", "Malaysian"],
    "Maldives": ["MV", "Maldivian"],
    "Mali": ["ML", "Malian"],
    "Malta": ["MT", "Maltese"],
    "Mauritius": ["MU", "Mauritian"],
    "Mexico": ["MX", "Mexican"],
    "Moldova": ["MD", "Moldovan", "Moldova, Republic of", "Republic of Moldova"],
    "Monaco": ["MC", "Monegasque"],
    "Mongolia": ["MN", "Mongolian"],
    "Montenegro": ["ME", "Montenegrin"],
    "Morocco": ["MA", "Moroccan"],
    "Mozambique": ["MZ", "Mozambican"],
    "Myanmar": ["MM", "Burmese", "Burma"],
    "Namibia": ["NA", "Namibian"],
    "Nepal": ["NP", "Nepalese", "Nepali"],
    "Netherlands": ["NL", "Dutch", "The Netherlands", "Holland"],
    "New Zealand": ["NZ", "New Zealander"],
    "Nicaragua": ["NI", "Nicaraguan"],
    "Niger": ["NE", "Nigerien"],
    "Nigeria": ["NG", "Nigerian"],
    "North Korea": ["KP", "Korea, Democratic People's Republic of", "Democratic People's Republic of Korea", "North Korean"],
    "North Macedonia": ["MK", "Macedonia", "Macedonian"],
    "Northern Mariana Islands": ["MP"],
    "Norway": ["NO", "Norwegian"],
    "Oman": ["OM", "Omani"],
    "Pakistan": ["PK", "Pakistani"],
    "Palestine": ["PS", "Palestinian", "Palestine, State of"],
    "Panama": ["PA", "Panamanian"],
    "Paraguay": ["PY", "Paraguayan"],
    "Peru": ["PE", "Peruvian"],
    "Philippines": ["PH", "Filipino", "Philippine"],
    "Poland": ["PL", "Polish"],
    "Portugal": ["PT", "Portuguese"],
    "Puerto Rico": ["PR", "Puerto Rican"],
    "Guam": ["GU"],
    "Qatar": ["QA", "Qatari"],
    "Romania": ["RO", "Romanian"],
    "Russia": ["RU", "Russian", "Russian Federation"],
    "Rwanda": ["RW", "Rwandan"],
    "Saudi Arabia": ["SA", "Saudi", "Saudi Arabian"],
    "Senegal": ["SN", "Senegalese"],
    "Serbia": ["RS", "Serbian"],
    "Sierra Leone": ["SL", "Sierra Leonean"],
    "Singapore": ["SG", "Singaporean"],
    "Slovakia": ["SK", "Slovak", "Slovakian"],
    "Slovenia": ["SI", "Slovenian", "Slovene"],
    "Somalia": ["SO", "Somali"],
    "South Africa": ["ZA", "South African"],
    "South Korea": ["KR", "Korea, Republic of", "Republic of Korea", "Korea", "South Korean", "Korean"],
    "South Sudan": ["SS", "South Sudanese"],
    "Spain": ["ES", "Spanish", "Spaniard"],
    "Sri Lanka": ["LK", "Sri Lankan"],
    "Sudan": ["SD", "Sudanese"],
    "Sweden": ["SE", "Swedish", "Swede"],
    "Switzerland": ["CH", "Swiss"],
    "Syria": ["SY", "Syrian", "Syrian Arab Republic"],
    "Taiwan": ["TW", "Taiwanese", "Taiwan, Province of China"],
    "Tajikistan": ["TJ", "Tajik"],
    "Tanzania": ["TZ", "Tanzanian", "Tanzania, United Republic of"],
    "Thailand": ["TH", "Thai"],
    "Togo": ["TG", "Togolese"],
    "Trinidad and Tobago": ["TT", "Trinidadian"],
    "Tunisia": ["TN", "Tunisian"],
    "Turkey": ["TR", "Turkish", "Türkiye", "Turkiye"],
    "Turkmenistan": ["TM", "Turkmen"],
    "Uganda": ["UG", "Ugandan"],
    "Ukraine": ["UA", "Ukrainian"],
    "United Arab Emirates": ["AE", "UAE", "Emirati", "U.A.E."],
    "United Kingdom": ["GB", "UK", "U.K.", "British", "Great Britain", "Britain", "England", "Scotland", "Wales",
                       "Northern Ireland", "English", "Scottish", "Welsh",
                       "United Kingdom of Great Britain and Northern Ireland"],
    "United States": ["US", "USA", "U.S.", "U.S.A.", "American", "United States of America"],
    "Uruguay": ["UY", "Uruguayan"],
    "Uzbekistan": ["UZ", "Uzbek"],
    "Venezuela": ["VE", "Venezuelan", "Venezuela, Bolivarian Republic of"],
    "Vietnam": ["VN", "Vietnamese", "Viet Nam"],
    "Yemen": ["YE", "Yemeni"],
    "Zambia": ["ZM", "Zambian"],
    "Zimbabwe": ["ZW", "Zimbabwean"],
    "Virgin Islands, British": ["VG", "British Virgin Islands"],
    "Virgin Islands, U.S.": ["VI", "US Virgin Islands", "U.S. Virgin Islands"],
    "Cayman Islands": ["KY"],
    "Bermuda": ["BM", "Bermudian"],
    "Gibraltar": ["GI"],
    "Isle of Man": ["IM"],
    "Jersey": ["JE"],
    "Guernsey": ["GG"],
    "Greenland": ["GL"],
    "Faroe Islands": ["FO"],
    "Curacao": ["CW", "Curaçao"],
    "Aruba": ["AW"],
    "Kiribati": ["KI"],
    "Fiji": ["FJ", "Fijian"],
    "Papua New Guinea": ["PG"],
    "Samoa": ["WS", "Samoan"],
    "American Samoa": ["AS"],
    "Seychelles": ["SC"],
    "Eritrea": ["ER", "Eritrean"],
    "Djibouti": ["DJ"],
    "Chad": ["TD", "Chadian"],
    "Burundi": ["BI", "Burundian"],
    "Lesotho": ["LS"],
    "Eswatini": ["SZ", "Swaziland"],
    "Mauritania": ["MR", "Mauritanian"],
    "Cape Verde": ["CV", "Cabo Verde"],
    "Equatorial Guinea": ["GQ"],
    "Guinea-Bissau": ["GW"],
    "Central African Republic": ["CF"],
    "Bhutan": ["BT", "Bhutanese"],
    "Timor-Leste": ["TL", "East Timor"],
    "San Marino": ["SM"],
    "Vatican City": ["VA", "Holy See"],
    "Antigua and Barbuda": ["AG"],
    "Saint Kitts and Nevis": ["KN"],
    "Saint Lucia": ["LC"],
    "Saint Vincent and the Grenadines": ["VC"],
    "Grenada": ["GD"],
    "Dominica": ["DM"],
    "Suriname": ["SR", "Surinamese"],
}

# Names and nationality words in the country's own language.
_LOCAL = {
    "Germany": ["Deutschland", "Deutsch", "Deutsche", "Deutscher", "Bundesrepublik Deutschland"],
    "Spain": ["España", "Espana", "Española", "Español", "Espanola", "Espanol"],
    "Austria": ["Österreich", "Osterreich", "Österreichisch", "Österreicher"],
    "Poland": ["Polska", "Polski", "Polskie"],
    "Sweden": ["Sverige", "Svensk", "Svenska"],
    "Italy": ["Italia", "Italiana", "Italiano"],
    "Norway": ["Norge", "Noreg", "Norsk"],
    "Greece": ["Ellada", "Hellas", "Ellinikí", "Elliniki", "Hellenic Republic"],
    "Finland": ["Suomi", "Suomalainen"],
    "Denmark": ["Danmark", "Dansk"],
    "Netherlands": ["Nederland", "Nederlands", "Nederlandse"],
    "Switzerland": ["Schweiz", "Suisse", "Svizzera", "Svizra", "Confoederatio Helvetica", "Helvetia"],
    "Brazil": ["Brasil", "Brasileiro", "Brasileira"],
    "Peru": ["Perú", "Peruano", "Peruana"],
    "Mexico": ["México", "Mexicano", "Mexicana"],
    "Ireland": ["Éire", "Eire"],
    "Croatia": ["Hrvatska"],
    "Hungary": ["Magyarország", "Magyarorszag", "Magyar"],
    "Czech Republic": ["Česko", "Cesko", "Česká republika", "Ceska republika"],
    "France": ["République française", "Republique francaise", "Française", "Francaise", "Français", "Francais"],
    "Portugal": ["Português", "Portugues", "Portuguesa"],
    "Romania": ["România", "Român", "Română"],
    "Turkey": ["Türk"],
    "Japan": ["Nippon", "Nihon"],
    "China": ["Zhongguo"],
    "Russia": ["Rossiya", "Rossiia"],
    "Ukraine": ["Ukraina", "Ukrayina"],
    "Belgium": ["België", "Belgie", "Belgique", "Belgien"],
    "Iceland": ["Ísland"],
    "Lithuania": ["Lietuva"],
    "Latvia": ["Latvija"],
    "Estonia": ["Eesti"],
    "Slovenia": ["Slovenija"],
    "Slovakia": ["Slovensko"],
    "Serbia": ["Srbija"],
    "Bulgaria": ["Bulgariya", "Balgariya"],
    "Egypt": ["Misr"],
    "Morocco": ["Maroc", "Al-Maghrib"],
    "Algeria": ["Algérie", "Algerie"],
    "Cote D'Ivoire": ["Côte d’Ivoire"],
    "Chile": ["Chileno", "Chilena"],
    "Colombia": ["Colombiano", "Colombiana"],
    "Argentina": ["Argentino", "Argentina"],
}
for _name, _aliases in sorted(_LOCAL.items()):
    _TABLE.setdefault(_name, [])
    _TABLE[_name] = _TABLE[_name] + [a for a in _aliases if a not in _TABLE[_name]]

NOT_COUNTRIES = {"international", "europe", "global", "worldwide", "unknown", "none", "n/a", "na", "africa",
                 "asia", "middle east", "latin america", "eu", "european union"}


def _key(text):
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_LOOKUP = {}
_CODES = {}
for _name, _aliases in sorted(_TABLE.items()):
    _LOOKUP[_key(_name)] = _name
    for _alias in _aliases:
        if len(_alias) == 2 and _alias.isupper():
            _CODES[_alias] = _name
        else:
            _LOOKUP[_key(_alias)] = _name


def known(name):
    """True when a canonical name is one of the listed countries or territories."""
    return name in _TABLE


def canon(value):
    """Return the canonical country name for a name, code or nationality, or None.

    A place written with commas ("Ribeirao Pires, Sao Paulo, Brazil") gives its last part;
    a longer text that names exactly one country gives that country.
    """
    if value is None:
        return None
    text = str(value).strip()
    text = re.sub(r"\[[^\]]*\]", "", text).strip()  # address tags such as [Business]
    if not text:
        return None
    if text.upper() in _CODES and len(text) == 2:
        return _CODES[text.upper()]
    if "," in text:
        last = canon(text.split(",")[-1])
        if last and known(last):
            return last
    key = _key(text)
    if not key or key in NOT_COUNTRIES:
        return None
    if key in _LOOKUP:
        return _LOOKUP[key]
    if key.startswith("the ") and key[4:] in _LOOKUP:
        return _LOOKUP[key[4:]]
    inside = find_in_text(text)
    if len(inside) == 1:
        return inside[0]
    return " ".join(word.capitalize() for word in key.split())


# For free text: every alias of 4+ characters (and a few safe short ones), longest first.
_TEXT_TERMS = sorted(
    {k for k in _LOOKUP if len(k) >= 4 or k in ("uk", "usa", "uae")},
    key=lambda k: (-len(k), k),
)
_TEXT_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(t) for t in _TEXT_TERMS) + r")(?![a-z])")


def find_in_text(text):
    """Return canonical countries named in free text (names and nationality words)."""
    if not text:
        return []
    key = _key(text)
    found = []
    taken = []
    for match in _TEXT_RE.finditer(key):
        found.append(_LOOKUP[match.group(1)])
        taken.append(match.span())
    # a bare "US" in capitals (as in "the US") is a country; lower-case "us" is a pronoun
    for match in re.finditer(r"(?<![A-Za-z.])U\.?S\.?(?![A-Za-z])", text):
        found.append("United States")
    return sorted(set(found))
