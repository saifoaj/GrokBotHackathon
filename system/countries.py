"""Country names, ISO codes and nationalities mapped to one canonical name.

General reference data (not taken from any data file). Territories count as
their own country. Each line: canonical ; ISO2 ; ISO3 ; aliases.
An alias starting with "~" is accepted only as a whole structured field,
never when searching free text (it is too likely to be an ordinary word or
a person's name there). ISO codes are structured-only too.
"""

import re

from names import simple_text

_TABLE = """
Afghanistan;AF;AFG;Afghan
Aland Islands;AX;ALA;Aland
Albania;AL;ALB;Albanian
Algeria;DZ;DZA;Algerian
American Samoa;AS;ASM;American Samoan
Andorra;AD;AND;Andorran
Angola;AO;AGO;Angolan
Anguilla;AI;AIA;Anguillan
Antigua and Barbuda;AG;ATG;Antiguan,Barbudan,Antigua,Antigua & Barbuda
Argentina;AR;ARG;Argentine,Argentinian,Argentinean
Armenia;AM;ARM;Armenian
Aruba;AW;ABW;Aruban
Australia;AU;AUS;Australian,New South Wales
Austria;AT;AUT;Austrian
Azerbaijan;AZ;AZE;Azerbaijani,Azeri
Bahamas;BS;BHS;The Bahamas,Bahamian
Bahrain;BH;BHR;Bahraini
Bangladesh;BD;BGD;Bangladeshi
Barbados;BB;BRB;Barbadian,~Bajan
Belarus;BY;BLR;Belarusian,Byelorussia,Belorussia
Belgium;BE;BEL;Belgian
Belize;BZ;BLZ;Belizean
Benin;BJ;BEN;Beninese
Bermuda;BM;BMU;Bermudian
Bhutan;BT;BTN;Bhutanese
Bolivia;BO;BOL;Bolivian,Plurinational State of Bolivia
Bonaire;BQ;BES;Bonaire Sint Eustatius and Saba,Caribbean Netherlands
Bosnia and Herzegovina;BA;BIH;Bosnian,Herzegovinian,Bosnia,Bosnia-Herzegovina,Bosnia & Herzegovina
Botswana;BW;BWA;Motswana,Batswana,Botswanan
Brazil;BR;BRA;Brazilian,Brasil
British Virgin Islands;VG;VGB;Virgin Islands British,BVI
Brunei;BN;BRN;Bruneian,Brunei Darussalam
Bulgaria;BG;BGR;Bulgarian
Burkina Faso;BF;BFA;Burkinabe
Burundi;BI;BDI;Burundian
Cabo Verde;CV;CPV;Cape Verde,Cape Verdean
Cambodia;KH;KHM;Cambodian
Cameroon;CM;CMR;Cameroonian
Canada;CA;CAN;Canadian
Cayman Islands;KY;CYM;Caymanian
Central African Republic;CF;CAF;Central African
Chad;TD;TCD;~Chad,Chadian
Chile;CL;CHL;Chilean
China;CN;CHN;Chinese,People's Republic of China,~PRC
Colombia;CO;COL;Colombian
Comoros;KM;COM;Comorian
Congo;CG;COG;Republic of the Congo,Congo-Brazzaville,Congo Brazzaville,Congo Republic,Congolese
Democratic Republic of the Congo;CD;COD;DR Congo,DRC,Congo-Kinshasa,Congo Kinshasa,Congo Democratic Republic,Zaire,Congolese,~Congo
Cook Islands;CK;COK;Cook Islander
Costa Rica;CR;CRI;Costa Rican
Cote d'Ivoire;CI;CIV;Ivory Coast,Ivorian
Croatia;HR;HRV;Croatian
Cuba;CU;CUB;Cuban
Curacao;CW;CUW;Curacaoan
Cyprus;CY;CYP;Cypriot
Czech Republic;CZ;CZE;Czechia,Czech
Denmark;DK;DNK;Danish,~Dane
Djibouti;DJ;DJI;Djiboutian
Dominica;DM;DMA;Dominican
Dominican Republic;DO;DOM;Dominican
Ecuador;EC;ECU;Ecuadorian,Ecuadorean
Egypt;EG;EGY;Egyptian
El Salvador;SV;SLV;Salvadoran,Salvadorian
Equatorial Guinea;GQ;GNQ;Equatoguinean,Equatorial Guinean
Eritrea;ER;ERI;Eritrean
Estonia;EE;EST;Estonian
Eswatini;SZ;SWZ;Swaziland,Swazi
Ethiopia;ET;ETH;Ethiopian
Falkland Islands;FK;FLK;Falklands,Falkland Islander
Faroe Islands;FO;FRO;Faroese,Faroes
Fiji;FJ;FJI;Fijian
Finland;FI;FIN;Finnish,~Finn
France;FR;FRA;French
French Guiana;GF;GUF;Guianese
French Polynesia;PF;PYF;Tahiti
Gabon;GA;GAB;Gabonese
Gambia;GM;GMB;The Gambia,Gambian
Georgia;GE;GEO;Georgian
Germany;DE;DEU;German,Deutschland
Ghana;GH;GHA;Ghanaian
Gibraltar;GI;GIB;Gibraltarian
Greece;GR;GRC;Greek,Hellenic Republic
Greenland;GL;GRL;Greenlandic,Greenlander
Grenada;GD;GRD;Grenadian
Guadeloupe;GP;GLP;Guadeloupean
Guam;GU;GUM;Guamanian
Guatemala;GT;GTM;Guatemalan
Guernsey;GG;GGY;
Guinea;GN;GIN;Guinean
Guinea-Bissau;GW;GNB;Bissau-Guinean,Guinea Bissau
Guyana;GY;GUY;Guyanese
Haiti;HT;HTI;Haitian
Holy See;VA;VAT;Vatican,Vatican City
Honduras;HN;HND;Honduran
Hong Kong;HK;HKG;Hong Kong SAR,Hong Kong Special Administrative Region of China,Hong Kong China,Hongkonger
Hungary;HU;HUN;Hungarian
Iceland;IS;ISL;Icelandic,Icelander
India;IN;IND;Indian
Indonesia;ID;IDN;Indonesian
Iran;IR;IRN;Iranian,Islamic Republic of Iran,Iran Islamic Republic of,Persia
Iraq;IQ;IRQ;Iraqi
Ireland;IE;IRL;Irish,Republic of Ireland,Eire
Isle of Man;IM;IMN;Manx
Israel;IL;ISR;Israeli
Italy;IT;ITA;Italian,Italia
Jamaica;JM;JAM;Jamaican
Japan;JP;JPN;Japanese
Jersey;JE;JEY;~Jersey
Jordan;JO;JOR;~Jordan,Jordanian
Kazakhstan;KZ;KAZ;Kazakh,Kazakhstani
Kenya;KE;KEN;Kenyan
Kiribati;KI;KIR;I-Kiribati
Kosovo;XK;XKX;Kosovar,Kosovan
Kuwait;KW;KWT;Kuwaiti
Kyrgyzstan;KG;KGZ;Kyrgyz,Kyrgyzstani,Kirghizia,Kyrgyz Republic
Laos;LA;LAO;Lao,Laotian,Lao People's Democratic Republic,Lao PDR
Latvia;LV;LVA;Latvian
Lebanon;LB;LBN;Lebanese
Lesotho;LS;LSO;Basotho,Mosotho
Liberia;LR;LBR;Liberian
Libya;LY;LBY;Libyan
Liechtenstein;LI;LIE;Liechtensteiner
Lithuania;LT;LTU;Lithuanian
Luxembourg;LU;LUX;Luxembourger,Luxembourgish
Macao;MO;MAC;Macau,Macao SAR,Macao Special Administrative Region of China,Macau Special Administrative Region of China,Macao China,Macanese
Madagascar;MG;MDG;Malagasy
Malawi;MW;MWI;Malawian
Malaysia;MY;MYS;Malaysian
Maldives;MV;MDV;Maldivian
Mali;ML;MLI;Malian
Malta;MT;MLT;Maltese
Marshall Islands;MH;MHL;Marshallese
Martinique;MQ;MTQ;Martinican
Mauritania;MR;MRT;Mauritanian
Mauritius;MU;MUS;Mauritian
Mayotte;YT;MYT;Mahoran
Mexico;MX;MEX;Mexican
Micronesia;FM;FSM;Micronesian,Federated States of Micronesia
Moldova;MD;MDA;Moldovan,Republic of Moldova,Moldova Republic of
Monaco;MC;MCO;Monegasque,Monacan
Mongolia;MN;MNG;Mongolian
Montenegro;ME;MNE;Montenegrin
Montserrat;MS;MSR;Montserratian
Morocco;MA;MAR;Moroccan
Mozambique;MZ;MOZ;Mozambican
Myanmar;MM;MMR;Burma,Burmese
Namibia;;NAM;Namibian
Nauru;NR;NRU;Nauruan
Nepal;NP;NPL;Nepalese,Nepali
Netherlands;NL;NLD;Dutch,Holland,The Netherlands,Kingdom of the Netherlands
New Caledonia;NC;NCL;New Caledonian
New Zealand;NZ;NZL;New Zealander,~Kiwi
Nicaragua;NI;NIC;Nicaraguan
Niger;NE;NER;Nigerien
Nigeria;NG;NGA;Nigerian
Niue;NU;NIU;Niuean
North Korea;KP;PRK;Democratic People's Republic of Korea,DPRK,Korea North,Korea DPR,North Korean,Korean,~Korea
North Macedonia;MK;MKD;Macedonia,Macedonian,Republic of North Macedonia,FYROM
Northern Mariana Islands;MP;MNP;Northern Marianas,Saipan
Norway;NO;NOR;Norwegian
Oman;OM;OMN;Omani
Pakistan;PK;PAK;Pakistani
Palau;PW;PLW;Palauan
Palestine;PS;PSE;Palestina,Palestinian,State of Palestine,Palestinian Territories,Palestinian Territory,West Bank,Gaza,Gaza Strip
Panama;PA;PAN;Panamanian
Papua New Guinea;PG;PNG;Papua New Guinean,Papuan
Paraguay;PY;PRY;Paraguayan
Peru;PE;PER;Peruvian
Philippines;PH;PHL;Filipino,Filipina,Philippine,The Philippines
Poland;PL;POL;Polish,~Pole
Portugal;PT;PRT;Portuguese
Puerto Rico;PR;PRI;Puerto Rican
Qatar;QA;QAT;Qatari
Reunion;RE;REU;Reunionese
Romania;RO;ROU;Romanian,Roumania
Russia;RU;RUS;Russian,Russian Federation
Rwanda;RW;RWA;Rwandan
Saint Barthelemy;BL;BLM;St Barthelemy,St Barts
Saint Helena;SH;SHN;St Helena,Saint Helenian
Saint Kitts and Nevis;KN;KNA;Kittitian,Nevisian,St Kitts and Nevis,St Kitts & Nevis,Saint Kitts & Nevis
Saint Lucia;LC;LCA;Saint Lucian,St Lucia,St Lucian
Saint Martin;MF;MAF;St Martin
Saint Pierre and Miquelon;PM;SPM;St Pierre and Miquelon
Saint Vincent and the Grenadines;VC;VCT;Vincentian,St Vincent and the Grenadines,St Vincent
Samoa;WS;WSM;Samoan
San Marino;SM;SMR;Sammarinese
Sao Tome and Principe;ST;STP;Sao Tomean,Sao Tome
Saudi Arabia;SA;SAU;Saudi,Saudi Arabian,Kingdom of Saudi Arabia,~KSA
Senegal;SN;SEN;Senegalese
Serbia;RS;SRB;Serbian,~Serb
Seychelles;SC;SYC;Seychellois
Sierra Leone;SL;SLE;Sierra Leonean
Singapore;SG;SGP;Singaporean
Sint Maarten;SX;SXM;Sint Maartener
Slovakia;SK;SVK;Slovak,Slovakian,Slovak Republic
Slovenia;SI;SVN;Slovenian,Slovene
Solomon Islands;SB;SLB;Solomon Islander
Somalia;SO;SOM;Somali,Somalian
South Africa;ZA;ZAF;South African
South Korea;KR;KOR;Republic of Korea,Korea South,Korea Republic of,South Korean,Korean,~Korea
South Sudan;SS;SSD;South Sudanese
Spain;ES;ESP;Spanish,Espana
Sri Lanka;LK;LKA;Sri Lankan,Ceylon
Sudan;SD;SDN;Sudanese
Suriname;SR;SUR;Surinamese,Surinam
Sweden;SE;SWE;Swedish,~Swede
Switzerland;CH;CHE;Swiss,Swiss Confederation
Syria;SY;SYR;Syrian,Syrian Arab Republic
Taiwan;TW;TWN;Taiwanese,Republic of China,Chinese Taipei,Taiwan Province of China
Tajikistan;TJ;TJK;Tajik,Tajikistani
Tanzania;TZ;TZA;Tanzanian,United Republic of Tanzania,Tanzania United Republic of
Thailand;TH;THA;Thai
Timor-Leste;TL;TLS;East Timor,Timorese,Timor Leste
Togo;TG;TGO;Togolese
Tonga;TO;TON;Tongan
Trinidad and Tobago;TT;TTO;Trinidadian,Tobagonian,Trinidad,Trinidad & Tobago
Tunisia;TN;TUN;Tunisian
Turkey;TR;TUR;Turkish,Turkiye,~Turk
Turkmenistan;TM;TKM;Turkmen
Turks and Caicos Islands;TC;TCA;Turks and Caicos,Turks & Caicos Islands
Tuvalu;TV;TUV;Tuvaluan
Uganda;UG;UGA;Ugandan
Ukraine;UA;UKR;Ukrainian
United Arab Emirates;AE;ARE;Emirati,UAE,Emirates,U.A.E.
United Kingdom;GB;GBR;British,~UK,U.K.,Great Britain,Britain,England,Scotland,Wales,Northern Ireland,English,Scottish,Welsh,United Kingdom of Great Britain and Northern Ireland,Briton
United States;US;USA;American,~USA,U.S.,U.S.A.,United States of America,~America,~US,New Jersey,New Mexico,New York
US Virgin Islands;VI;VIR;Virgin Islands US,United States Virgin Islands,U.S. Virgin Islands
Uruguay;UY;URY;Uruguayan
Uzbekistan;UZ;UZB;Uzbek,Uzbekistani
Vanuatu;VU;VUT;Ni-Vanuatu
Venezuela;VE;VEN;Venezuelan,Bolivarian Republic of Venezuela
Vietnam;VN;VNM;Vietnamese,Viet Nam
Wallis and Futuna;WF;WLF;Wallisian
Western Sahara;EH;ESH;Sahrawi
Yemen;YE;YEM;Yemeni
Zambia;ZM;ZMB;Zambian
Zimbabwe;ZW;ZWE;Zimbabwean
"""

# Regions and labels that are not countries (principle 5).
NOT_COUNTRIES = frozenset(simple_text(x) for x in [
    "International", "Europe", "European Union", "EU", "Global", "Worldwide",
    "Africa", "Asia", "Middle East", "Latin America", "South America",
    "North America", "Central America", "Caribbean", "Oceania", "Balkans",
    "Scandinavia", "Gulf", "West Africa", "East Africa", "Southeast Asia",
    "Unknown", "N/A", "NA", "None", "Other", "Not Known", "Stateless",
])

# Uppercase codes accepted inside free text (case-sensitive, whole word).
_TEXT_CODES = {"US": "United States", "USA": "United States", "UK": "United Kingdom",
               "UAE": "United Arab Emirates", "DRC": "Democratic Republic of the Congo"}

_FIELD = {}    # simple_text(alias or code) -> sorted tuple of canonical names
_TEXT = {}     # simple_text(alias) -> sorted tuple, usable in free text


def _add(table, key, canonical):
    if not key:
        return
    names = set(table.get(key, ()))
    names.add(canonical)
    table[key] = tuple(sorted(names))


def _build():
    for line in _TABLE.strip().splitlines():
        canonical, iso2, iso3, aliases = (line.split(";") + ["", "", ""])[:4]
        canonical = canonical.strip()
        for key in (canonical, iso2.strip(), iso3.strip()):
            _add(_FIELD, simple_text(key), canonical)
        _add(_TEXT, simple_text(canonical), canonical)
        for alias in aliases.split(","):
            alias = alias.strip()
            if not alias:
                continue
            field_only = alias.startswith("~")
            alias = alias.lstrip("~")
            _add(_FIELD, simple_text(alias), canonical)
            if not field_only:
                _add(_TEXT, simple_text(alias), canonical)


_build()

# Regions are in the same pattern mapping to nothing, so "latin america"
# never yields a country while "south africa" still does.
for _region in sorted(NOT_COUNTRIES):
    if len(_region) > 2 and _region not in _TEXT:
        _TEXT[_region] = ()

# Longest aliases first so "south sudan" wins over "sudan".
_TEXT_REGEX = re.compile(
    r"(?<![0-9a-z])(" + "|".join(re.escape(a) for a in sorted(_TEXT, key=lambda a: (-len(a), a)))
    + r")(?![0-9a-z])")
_CODE_REGEX = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted(_TEXT_CODES, key=lambda c: (-len(c), c))) + r")(?![A-Za-z])")
_BRACKETS = re.compile(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}")


def lookup(value):
    """Canonical countries for a structured field value.

    Returns a sorted tuple (usually one name; an ambiguous nationality such as
    "Congolese" gives two), () when the value is empty or not a country such
    as "International", or None when it is text we do not recognise.
    """
    if not isinstance(value, str):
        return ()
    text = simple_text(_BRACKETS.sub(" ", value))
    if not text:
        return ()
    if text in NOT_COUNTRIES:
        return ()
    if text in _FIELD:
        return _FIELD[text]
    if text.startswith("the ") and text[4:] in _FIELD:
        return _FIELD[text[4:]]
    return None


def find_in_text(text):
    """Countries named inside free text, as a sorted tuple of canonical names."""
    if not isinstance(text, str) or not text:
        return ()
    found = set()
    for match in _CODE_REGEX.finditer(text):
        found.add(_TEXT_CODES[match.group(1)])
    simple = simple_text(text)
    for match in _TEXT_REGEX.finditer(simple):
        found.update(_TEXT[match.group(1)])
    return tuple(sorted(found))
