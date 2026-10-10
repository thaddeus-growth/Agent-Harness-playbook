"""Two SYNTHETIC client workbooks and their maps, for the workbook tests
(not a test file: no test_ prefix). Every name, code, address and number
here is invented.

  im(version)  an influencer-programme tracker shaped like a real one: a
               label list with a side-by-side block whose headers are
               formulas (header row 2), a roster under a counters block
               with a merged group row (header row 16), a quarter sheet
               with a merged group row (header row 2), blank rows inside,
               a hidden row, a duplicate key, a code with a trailing
               newline, a formula with no saved value and a QUICK SUMMARY
               block below, a calendar of text date ranges (header row 3),
               and a hidden list sheet behind a dropdown. Version 2 is the
               same workbook saved later with edits (for the change record).
  lumi()       "Lumi Haircare Nordics": one sheet per market (DK, SE, NO)
               with headers on row 1 in Danish, Swedish and Norwegian, a
               campaign column merged down its rows, real dates in the
               1904 date system, a status dropdown fed by a hidden sheet,
               and a payments sheet whose header sits on row 4 under a
               title block, keyed by invoice number.

PERSONAL holds every planted personal value; no output may show one.
"""

from __future__ import annotations

from kit.testing.xlsx import D, E, F, build

PERSONAL = ("creator.one@example.invalid", "creator.two@example.invalid",
            "creator.two.new@example.invalid", "creator.three@example.invalid",
            "creator.four@example.invalid", "creator.five@example.invalid",
            "creator.six@example.invalid", "agent.x@example.invalid",
            "astrid@example.invalid", "bjorn@example.invalid",
            "cecilie@example.invalid", "dag@example.invalid",
            "elin@example.invalid", "frode@example.invalid",
            "gudrun@example.invalid", "Creator One", "Creator Two",
            "Creator Three", "Creator Four", "Creator Five", "Creator Six",
            "Astrid Holm", "Bjorn Lund", "Cecilie Berg", "Dag Vik",
            "Elin Sand", "Frode Dal", "Gudrun Lie", "Test Author",
            "Later Author", "https://example.com/page/",
            "https://example.com/ig/")

FIELDS = {"roster.month", "roster.name", "roster.page", "roster.email",
          "roster.code", "roster.ads_code", "roster.fee", "roster.fee_local",
          "roster.label", "roster.tier_band", "roster.agency",
          "roster.posted_on", "roster.notes", "roster.term_ads",
          "roster.term_assets", "roster.flag.data_login",
          "roster.flag.assets_uploaded", "roster.flag.partnership_upload",
          "roster.flag.reels", "roster.priority", "roster.campaign",
          "roster.status", "label.number", "label.direction", "label.text",
          "label.text_en", "calendar.event", "calendar.window",
          "calendar.discount", "calendar.push", "payment.invoice",
          "payment.name", "payment.amount", "payment.paid_on"}


def _uses() -> dict:
    rows = [("#1", "Swap", "Dorm", "#1 | Swap | Dorm"),
            ("#2", "Swap", "Rental", "#2 | Swap | Rental"),
            ("#3", "Swap", "Old bed", "#3 | Swap | Old bed"),
            ("#4", "New", "New home", "#4 | New | New home"),
            ("#5", "Problem", "Back pain", "#5 | Problem | Back pain"),
            ("#6", "Other", "Mood", "#5 | Other | Mood")]   # the id clash
    cells = {"A1": "DO NOT EDIT", "A2": "NO.", "B2": "Direction",
             "C2": "Content", "D2": "LABEL", "E2": "Eng LABEL",
             "H2": F('"Counts (Q1)"', "Counts (Q1)"),
             "I2": F('"Product A"', "Product A"),
             "J2": F('"Product B"', "Product B")}
    for i, (n, d, c, en) in enumerate(rows, 3):
        cells.update({f"A{i}": n, f"B{i}": d, f"C{i}": c,
                      f"D{i}": F(f'A{i}&"|"&B{i}&"|"&C{i}', f"{n}|{d}|{c}"),
                      f"E{i}": en})
    for i, d in enumerate(("Swap", "New", "Problem"), 3):
        cells.update({f"H{i}": d, f"I{i}": F(f"COUNTIF(B:B,H{i})", i),
                      f"J{i}": F(f"COUNTIF(C:C,H{i})", 0)})
    return {"name": "Use cases", "cells": cells}


CREATORS = [("Jan", "Swap", "#1|Swap|Dorm", "Creator One", 1, 15000,
             "nano01", False, False, "students"),
            ("Jan", "New", "#4|New|New home", "Creator Two", 2, 15000,
             "nano02", True, True, ""),
            ("Feb", "Problem", "", "Creator Three", 3, 15000, "nano03",
             True, False, "no voice-over"),
            ("Feb", "Swap", "#3|Swap|Old bed", "Creator Four", 4, 15000,
             "nano04", False, False, ""),
            ("Mar", "Other", "#6|Other|Mood", "Creator Five", 5, 15000,
             "nano05", True, True, "")]
EMAILS = {1: "creator.one@example.invalid", 2: "creator.two@example.invalid",
          3: "creator.three@example.invalid",
          4: "creator.four@example.invalid", 5: "creator.five@example.invalid",
          6: "creator.six@example.invalid"}


def _creators(version: int) -> dict:
    rows = list(CREATORS)
    if version == 2:
        rows = [r for r in rows if r[6] != "nano05"]
        rows.append(("Apr", "New", "#2|Swap|Rental", "Creator Six", 6, 15000,
                     "nano06", False, False, ""))
    cells = {"A1": "Plan: 20 a month (SYNTHETIC)", "G3": "Posts released",
             "H3": F("COUNTIF(N17:N40,TRUE)", 3), "G4": "To be released",
             "H4": F("20-H3", 17), "A15": "Basic Info", "J15": "Ads Setting"}
    heads = ["Month", "Direction", "USE CASE LABEL", "Name", "Page", "Email",
             "Cost-NTD", "Cost-EU", "Notes", "Partnership ads", "Assets",
             "Original Code", "Ads Code", "Data Login", "Assets uploaded"]
    if version == 2:
        heads.append("Priority")
    for k, h in enumerate(heads):
        cells[f"{chr(65 + k)}16"] = h
    links, comments = {}, {}
    last = 16 + len(rows)
    for i, (m, d, lab, name, n, ntd, code, login, up, note) in enumerate(
            rows, 17):
        email = EMAILS[n]
        if version == 2 and n == 2:
            email = "creator.two.new@example.invalid"
        if version == 2 and n == 1:
            login, ntd = True, 16000
        if version == 2 and n == 3:
            note = "write to agent.x@example.invalid"
        cells.update({
            f"A{i}": m, f"B{i}": d, f"C{i}": lab or None, f"D{i}": name,
            f"E{i}": f"https://example.com/page/{n}", f"F{i}": email,
            f"G{i}": ntd,
            f"H{i}": F(f"G{i}/38", round(ntd / 38, 2), shared="0",
                       master=i == 17, ref=f"H17:H{last}"),
            f"I{i}": note or None, f"J{i}": "3 months", f"K{i}": "2 years",
            f"L{i}": code, f"M{i}": "a" + code, f"N{i}": login,
            f"O{i}": up})
        if version == 2:
            cells[f"P{i}"] = "high" if n == 1 else None
        links[f"E{i}"] = f"https://example.com/page/{n}"
    comments["G18"] = "fee checked"
    return {"name": "Creators", "cells": cells,
            "merged": ["A15:I15", "J15:O15"], "links": links,
            "comments": comments,
            "validations": [{"sqref": "N17:O40", "type": "list",
                             "formula1": '"TRUE,FALSE"'}]}


def _quarter() -> dict:
    cells = {"A1": "1. gifting theme (SYNTHETIC)", "J1": "Activity Type"}
    heads = ["Month", "KOL", "Tier", "Agency", "Coupon", "Cost-NTD",
             "Cost-Euro", "Usecase_Label", "Estimated Launch Date",
             "IG Reels", "IG Post", "FB", "Partnership upload"]
    for k, h in enumerate(heads):
        cells[f"{chr(65 + k)}2"] = h
    data = {3: ("Jan", "Studio North", "Macro(100k-499k)", "SUN", "STUDIO10",
                50000, "#1|Swap|Dorm", D("2026-01-03"), 1, 0, 0, True),
            4: ("Jan", "Ring Talk", "Nano (0-19k)", "Inhouse", "RING01\n",
                5000, "#3|Swap|Old bed", D("2026-01-20"), 0, 1, 0, False),
            5: ("Feb", "Plan Only", "Micro(20-99k)", "", "PLAN01", None, "",
                None, 0, 0, 0, False),
            7: ("Feb", "Twin Posts", "Mega(>500k)", "REDI", "TWIN",
                120000, "", D("2026-02-14"), 1, 1, 0, True),
            8: ("Feb", "Twin Posts", "Mega(>500k)", "REDI", "TWIN",
                90000, "", D("2026-02-28"), 0, 0, 1, False),
            9: ("Mar", "Hidden Row", "Nano (0-19k)", "SUN", "HID01",
                8000, "#5|Problem|Back pain", D("2026-03-02"), 1, 0, 0,
                False),
            10: ("Mar", "Late Save", "Micro(20-99k)", "Creator DB", "LATE01",
                 20000, "", D("2026-03-30", "custom"), 0, 1, 0, False)}
    for r, (m, kol, tier, ag, cp, ntd, lab, day, reels, post, fb, up) in \
            data.items():
        cells.update({f"A{r}": m, f"B{r}": kol, f"C{r}": tier,
                      f"D{r}": ag or None, f"E{r}": cp, f"F{r}": ntd,
                      f"H{r}": lab or None, f"I{r}": day, f"J{r}": reels,
                      f"K{r}": post, f"L{r}": fb, f"M{r}": up})
        if ntd is not None:
            cells[f"G{r}"] = F(f"F{r}/38", round(ntd / 38, 2))
    cells["G10"] = F("F10/38", None)           # saved with no value
    cells.update({"A12": "QUICK SUMMARY (DO NOT EDIT)",
                  "A14": "Overall Summary", "B14": "Count",
                  "E14": "Tier (Q1)", "F14": "Count",
                  "I14": "Spending (euro)", "J14": "Sum",
                  "A15": "Influencer", "B15": F("COUNTA(B3:B10)", 7),
                  "E15": "Nano (0-19k)", "F15": F('COUNTIF(C3:C10,E15)', 2),
                  "I15": "Jan", "J15": F("SUM(G3:G4)", 1447.37),
                  "L15": E("#DIV/0!")})
    return {"name": "Q1", "cells": cells,
            "merged": ["J1:L1", "A12:M12"], "hidden_rows": [9],
            "validations": [{"sqref": "C3:C10", "type": "list",
                             "formula1": "Lists!$A$1:$A$4"}]}


def _calendar() -> dict:
    cells = {"A1": "Discount Calendar", "B1": "source workbook",
             "B3": "Event", "C3": "Post Releasing Date", "D3": "DTC Campaign",
             "E3": "DTC Discount", "F3": "Key Push",
             "B4": "New Year", "C4": "1/6～1/24", "D4": "1/6～1/26",
             "E4": "45% off", "F4": "Product A",
             "A5": "2/8 cut-off", "B5": "Spring", "C5": "2/8-3/1",
             "D5": "2/8-3/1", "E5": "40% off", "F5": "Product B",
             "B6": "Someday", "C6": "soon", "E6": "", "F6": "Product A"}
    return {"name": "Calendar", "cells": cells,
            "links": {"B1": "https://example.com/calendar.xlsx"}}


def _lists() -> dict:
    return {"name": "Lists", "state": "hidden",
            "cells": {"A1": "Nano (0-19k)", "A2": "Micro(20-99k)",
                      "A3": "Macro(100k-499k)", "A4": "Mega(>500k)"}}


def im(version: int = 1) -> bytes:
    """The SYNTHETIC influencer-programme workbook, as saved."""
    props = {"modified": "2026-10-09T10:44:00Z"} if version == 1 else \
        {"modified": "2026-10-16T09:00:00Z", "modified_by": "Later Author"}
    sheets = [_uses(), _creators(version), _quarter(), _calendar(), _lists()]
    if version == 2:
        sheets[0]["cells"]["D3"] = F('CONCAT(A3,"|",B3,"|",C3)',
                                     "#1|Swap|Dorm")
    return build(sheets, props=props)


IM_TABLES = """\
table_id\tworkbook\tsheet\tanchor\tsearch\tstop\tkey\tmarket\ttarget\tstatus\tnote
labels\tim\tUse cases\tLABEL\trows 1-5\t\tNO.\t\t-\tconfirmed\tthe label list
label_counts\tim\tUse cases\tCounts (Q1)\trows 1-5\t\tCounts (Q1)\t\t-\tproposed\tthe side-by-side block
roster\tim\tCreators\tName\trows 1-30\t\tOriginal Code\tTW\troster\tconfirmed\t
q1\tim\tQ1\tUsecase_Label\trows 1-5\tQUICK SUMMARY\tMonth + KOL + Coupon\tTW\troster\tconfirmed\t
calendar\tim\tCalendar\tEvent\trows 1-5\t\tEvent\t\tcalendar\tconfirmed\t
"""

IM_COLUMNS = """\
table_id\theader\tfield\tdirection\towner\ttype\tstatus\tnote
labels\tNO.\tlabel.number\tin\t\ttext\tconfirmed\t
labels\tDirection\tlabel.direction\tin\t\ttext\tconfirmed\t
labels\tLABEL\tlabel.text\tin\t\tlabel\tconfirmed\t
labels\tEng LABEL\tlabel.text_en\tin\t\tlabel\tconfirmed\t
label_counts\tCounts (Q1)\t-\tin\t\ttext\tproposed\t
roster\tMonth\troster.month\tin\t\ttext\tconfirmed\t
roster\tUSE CASE LABEL\troster.label\tin\t\tlabel\tconfirmed\t
roster\tName\troster.name\tin\t\tperson\tconfirmed\t
roster\tPage\troster.page\tin\t\turl\tconfirmed\t
roster\tEmail\troster.email\tin\t\temail\tconfirmed\t
roster\tCost-NTD\troster.fee_local\tin\t\tmoney:TWD\tconfirmed\t
roster\tCost-EU\troster.fee\tin\t\tmoney:EUR\tconfirmed\t
roster\tNotes\troster.notes\tin\t\ttext\tconfirmed\t
roster\tPartnership ads\troster.term_ads\tin\t\ttext\tconfirmed\t
roster\tAssets\troster.term_assets\tin\t\ttext\tconfirmed\t
roster\tOriginal Code\troster.code\tin\t\tcode\tconfirmed\t
roster\tAds Code\troster.ads_code\tin\t\tcode\tconfirmed\t
roster\tData Login\troster.flag.data_login\tboth\tharness-a\tbool\tconfirmed\t
roster\tAssets uploaded\troster.flag.assets_uploaded\tout\tharness-a\tbool\tproposed\twaits on the owner
q1\tMonth\troster.month\tin\t\ttext\tconfirmed\t
q1\tKOL\troster.name\tin\t\tperson\tconfirmed\t
q1\tTier\troster.tier_band\tin\t\ttext\tconfirmed\t
q1\tAgency\troster.agency\tin\t\ttext\tconfirmed\t
q1\tCoupon\troster.code\tin\t\tcode\tconfirmed\t
q1\tCost-Euro\troster.fee\tin\t\tmoney:EUR\tconfirmed\t
q1\tUsecase_Label\troster.label\tin\t\tlabel\tconfirmed\t
q1\tEstimated Launch Date\troster.posted_on\tin\t\tdate\tconfirmed\t
q1\tActivity Type > IG Reels\troster.flag.reels\tin\t\tint\tconfirmed\t
q1\tPartnership upload\troster.flag.partnership_upload\tboth\tharness-b\tbool\tconfirmed\t
calendar\tEvent\tcalendar.event\tin\t\ttext\tconfirmed\t
calendar\tPost Releasing Date\tcalendar.window\tin\t\tmd_range\tconfirmed\t
calendar\tDTC Discount\tcalendar.discount\tin\t\ttext\tconfirmed\t
calendar\tKey Push\tcalendar.push\tin\t\ttext\tconfirmed\t
-\t-\tpayment.invoice\tin\t\ttext\tconfirmed\tthis workbook keeps no payments
"""


def _market_sheet(name: str, heads: list[str], people: list[tuple]) -> dict:
    cells = {f"{chr(65 + k)}1": h for k, h in enumerate(heads)}
    for i, (who, mail, code, fee, camp, day, status) in enumerate(people, 2):
        cells.update({f"A{i}": who,
                      f"B{i}": f"https://example.com/ig/{code.lower()}",
                      f"C{i}": mail, f"D{i}": code, f"E{i}": fee,
                      f"F{i}": camp, f"G{i}": D(day), f"H{i}": status})
    # a campaign is written once and merged down its rows
    groups: list[list[int]] = []
    for i, p in enumerate(people, 2):
        if p[4] is not None:
            groups.append([i, i])
        else:
            groups[-1][1] = i
    merged = [f"F{a}:F{b}" for a, b in groups if b > a]
    return {"name": name, "cells": cells, "merged": merged,
            "validations": [{"sqref": "H2:H50", "type": "list",
                             "formula1": "Lister!$A$1:$A$3"}]}


def lumi() -> bytes:
    """The SYNTHETIC Lumi Haircare Nordics tracker (1904 date system)."""
    dk = _market_sheet("DK", ["Creator", "Instagram", "E-mail", "Rabatkode",
                              "Honorar (DKK)", "Kampagne", "Publiceret",
                              "Status"],
                       [("Astrid Holm", "astrid@example.invalid", "ASTRID15",
                         4500, "Forår", "2026-03-02", "Aktiv"),
                        ("Bjorn Lund", "bjorn@example.invalid", "BJORN15",
                         3800, None, "2026-03-09", "Aktiv"),
                        ("Cecilie Berg", "cecilie@example.invalid", "CECI15",
                         5200, "Sommer", "2026-06-01", "Afsluttet"),
                        ("Dag Vik", "dag@example.invalid", "DAG15", 0,
                         None, "2026-06-15", "Pause")])
    se = _market_sheet("SE", ["Creator", "Instagram", "E-post", "Rabattkod",
                              "Arvode (SEK)", "Kampanj", "Publicerad",
                              "Status"],
                       [("Elin Sand", "elin@example.invalid", "ELIN10",
                         6000, "Vår", "2026-04-01", "Aktiv"),
                        ("Frode Dal", "frode@example.invalid", "FRODE10",
                         6500, None, "2026-04-20", "Aktiv")])
    no = _market_sheet("NO", ["Creator", "Instagram", "E-post", "Rabattkode",
                              "Honorar (NOK)", "Kampanje", "Publisert",
                              "Status"],
                       [("Gudrun Lie", "gudrun@example.invalid", "GUDRUN20",
                         7000, "Vår", "2026-05-05", "Aktiv")])
    pay = {"name": "Betalinger", "merged": ["A1:D1"],
           "cells": {"A1": "Betalinger 2026 (SYNTHETIC)",
                     "A2": "Opdateret ugentligt", "A4": "Faktura nr",
                     "B4": "Creator", "C4": "Beløb", "D4": "Betalt",
                     "A5": "F-1001", "B5": "Astrid Holm", "C5": 4500,
                     "D5": D("2026-03-31"), "A6": "F-1002",
                     "B6": "Bjorn Lund", "C6": 3800, "D6": D("2026-04-30"),
                     "A7": "F-1003", "B7": "Elin Sand", "C7": 6000,
                     "D7": D("2026-05-29")}}
    lister = {"name": "Lister", "state": "hidden",
              "cells": {"A1": "Aktiv", "A2": "Afsluttet", "A3": "Pause"}}
    return build([dk, se, no, pay, lister], date1904=True,
                 props={"modified": "2026-09-30T08:00:00Z",
                        "modified_by": "Gudrun Lie"})


LUMI_TABLES = """\
table_id\tworkbook\tsheet\tanchor\tsearch\tstop\tkey\tmarket\ttarget\tstatus\tnote
dk\tlumi\tDK\tRabatkode\trows 1-3\t\tRabatkode\tDK\troster\tconfirmed\t
se\tlumi\tSE\tRabattkod\trows 1-3\t\tRabattkod\tSE\troster\tconfirmed\t
no\tlumi\tNO\tRabattkode\trows 1-3\t\tRabattkode\tNO\troster\tconfirmed\t
pay\tlumi\tBetalinger\tFaktura nr\trows 1-10\t\tFaktura nr\t\tpayments\tconfirmed\t
"""

LUMI_COLUMNS = "table_id\theader\tfield\tdirection\towner\ttype\tstatus\tnote\n" \
    + "".join(
        f"{t}\tCreator\troster.name\tin\t\tperson\tconfirmed\t\n"
        f"{t}\tInstagram\troster.page\tin\t\turl\tconfirmed\t\n"
        f"{t}\t{mail}\troster.email\tin\t\temail\tconfirmed\t\n"
        f"{t}\t{code}\troster.code\tin\t\tcode\tconfirmed\t\n"
        f"{t}\t{fee}\troster.fee\tin\t\tmoney:{cur}\tconfirmed\t\n"
        f"{t}\t{camp}\troster.campaign\tin\t\ttext\tconfirmed\t\n"
        f"{t}\t{day}\troster.posted_on\tin\t\tdate\tconfirmed\t\n"
        f"{t}\tStatus\troster.status\tboth\tharness-a\ttext\tconfirmed\t\n"
        for t, mail, code, fee, cur, camp, day in (
            ("dk", "E-mail", "Rabatkode", "Honorar (DKK)", "DKK", "Kampagne",
             "Publiceret"),
            ("se", "E-post", "Rabattkod", "Arvode (SEK)", "SEK", "Kampanj",
             "Publicerad"),
            ("no", "E-post", "Rabattkode", "Honorar (NOK)", "NOK",
             "Kampanje", "Publisert"))) \
    + "pay\tFaktura nr\tpayment.invoice\tin\t\ttext\tconfirmed\t\n" \
    + "pay\tCreator\tpayment.name\tin\t\tperson\tconfirmed\t\n" \
    + "pay\tBeløb\tpayment.amount\tin\t\tmoney:DKK\tconfirmed\t\n" \
    + "pay\tBetalt\tpayment.paid_on\tin\t\tdate\tconfirmed\t\n" \
    + "-\t-\troster.label\tin\t\tlabel\tconfirmed\tno use-case column in " \
      "this workbook\n"
