# 🎓 moodle-mcp

<div dir="rtl">

**מדברים עם ה-Moodle.** שרת [MCP](https://modelcontextprotocol.io) קטן שנותן ל-Claude לקרוא את הקורסים, המטלות, הציונים והקבצים שלכם — שואלים במקום ללחוץ.

[English below ↓](#english)

</div>

![moodle-mcp demo](demo/demo.gif)

<div dir="rtl">

<sub>הדמו משתמש בנתוני דוגמה. [הסרטון המלא](demo/demo.mp4)</sub>

> "מה יש לי להגיש השבוע?"<br>
> "תוריד לי את שיעורי הבית החדשים במעגלים אנלוגיים"<br>
> "יש הודעות חדשות מהמרצים?"<br>
> "תסביר לי את הפרויקט החדש במבנה המחשב"

נבנה באוניברסיטת תל אביב, עם הגדרות מוכנות ל-37 אוניברסיטאות ומכללות בארץ. לקריאה בלבד: לא מגיש, לא מפרסם ולא משנה שום דבר.

## ⚡ הדרך הקלה: תנו ל-Claude להתקין

פותחים את [Claude Code](https://claude.com/claude-code) וכותבים לו:

</div>

```text
אני לומד בטכניון, תתקין לי את https://github.com/raztassa2000-eng/moodle-mcp
```

<div dir="rtl">

משם Claude יוריד את הפרויקט, יתקין אותו, יבחר את המוסד שלכם ויחבר את השרת ל-Claude Code, וגם ל-Claude Desktop אם הוא מותקן אצלכם. בסוף הוא יבקש מכם דבר אחד: להריץ פקודת התחברות אחת בטרמינל (שלב 2 למטה). סיסמה ומפתח לא מדביקים בצ'אט.

**איפה זה עובד:** ב-Claude Code וב-Claude Desktop (מק וווינדוס). השרת רץ על המחשב שלכם, ולכן הוא לא זמין באתר claude.ai ובאפליקציה בטלפון.

## 🧰 מה הוא יודע לעשות

| כלי | מה הוא עושה |
| --- | --- |
| `whats_new` | מה התחדש בכל הקורסים בימים האחרונים: קבצים, מטלות והודעות, בקריאה אחת |
| `list_courses` | הקורסים שאתם רשומים אליהם |
| `course_contents` | יחידות, פעילויות וקבצים של קורס |
| `upcoming_deadlines` | מטלות ובחנים בימים הקרובים |
| `calendar_events` | כל מה שבלוח השנה של Moodle: מבחנים, אירועים ומועדי הגשה |
| `assignments` | תאריכי הגשה, סטטוס ההגשה שלכם, הציון, הערות הבודק וקבצים מצורפים |
| `quizzes` | בחנים: מועדי פתיחה וסגירה, הגבלת זמן, הניסיונות והציון שלכם |
| `grades` | הציונים שלכם בקורס |
| `grades_overview` | הציון הסופי בכל הקורסים, בקריאה אחת |
| `forum_discussions` | הודעות אחרונות בפורומים ובלוח ההודעות |
| `forum_thread` | שרשור מלא בפורום, כולל כל התגובות |
| `notifications` | ההתראות שלכם ב-Moodle (הפעמון) |
| `download_file` | שמירת קובץ מהקורס ל-`~/Downloads/moodle` |
| `download_course` | הורדת כל הקבצים של קורס לתיקייה מסודרת לפי יחידות; קבצים שכבר ירדו ולא השתנו מדולגים |

## 🚀 התקנה ידנית

### 1. מורידים ומתקינים

</div>

```bash
git clone https://github.com/raztassa2000-eng/moodle-mcp.git
cd moodle-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

<div dir="rtl">

### 2. מתחברים

מריצים בטרמינל מתוך תיקיית `moodle-mcp` (בטרמינל חדש, קודם `cd` אליה), ומחליפים את `tau` במפתח של המוסד שלכם:

</div>

```bash
.venv/bin/pip install playwright
MOODLE_SITE=tau .venv/bin/python moodle_mcp.py --login
```

<div dir="rtl">

הכלי בוחר לבד את דרך ההתחברות של המוסד שלכם:

- **בדפדפן:** נפתח חלון של Chrome או Edge, ומתחברים כרגיל עם פרטי האוניברסיטה. ככה זה בתל אביב, בטכניון ובעוד 20 מוסדות.
- **שם משתמש וסיסמה:** הכלי שואל אותם בטרמינל. הסיסמה מוסתרת, נשלחת רק ל-Moodle של המוסד ולא נשמרת. ככה זה בבן-גוריון, בבצלאל, ברופין ועוד.

בשתי הדרכים לא צריך ללחוץ על "איפוס". מקבלים את המפתח הקיים, כך שאפליקציית Moodle בטלפון נשארת מחוברת. המפתח נשמר בקובץ `.token`, שלא נכנס ל-git, ולא מוצג על המסך. נבדק מקצה לקצה רק בתל אביב.

<details>
<summary>לא עבד? הדרך הידנית</summary>

ב-Moodle: תמונת הפרופיל ← **העדפות** ← **מפתחות אבטחה**. בשורה של *Moodle mobile web service* לוחצים על **איפוס**, ורק אז מוצג המפתח המלא. מעתיקים אותו ומריצים בתיקיית הפרויקט את הפקודה שמתאימה למחשב שלכם:

- במק: `pbpaste > .token && chmod 600 .token`
- בווינדוס (PowerShell): `Get-Clipboard | Set-Content .token`

שימו לב: איפוס מבטל את המפתח הקודם, כך שאפליקציית Moodle בטלפון עשויה לבקש התחברות מחדש. אפשר גם להשתמש במשתנה הסביבה `MOODLE_TOKEN`.

</details>

### 3. מחברים ל-Claude

ב-**Claude Code** (מחליפים את `tau` במפתח של המוסד שלכם):

</div>

```bash
claude mcp add moodle -s user -e MOODLE_SITE=tau -- "$PWD/.venv/bin/python" "$PWD/moodle_mcp.py"
```

<div dir="rtl">

ב-**Claude Desktop** פותחים הגדרות ← **Developer** ← **Edit Config**, מוסיפים את הבלוק הבא לקובץ `claude_desktop_config.json` ומפעילים מחדש את האפליקציה. אם כבר יש בקובץ `mcpServers`, מוסיפים רק את `moodle` לתוכו.

הקובץ נמצא כאן:

</div>

```text
macOS:    ~/Library/Application Support/Claude/claude_desktop_config.json
Windows:  %APPDATA%\Claude\claude_desktop_config.json
```

<div dir="rtl">

וזה הבלוק שמוסיפים:

</div>

```json
{
  "mcpServers": {
    "moodle": {
      "command": "/full/path/to/moodle-mcp/.venv/bin/python",
      "args": ["/full/path/to/moodle-mcp/moodle_mcp.py"],
      "env": { "MOODLE_SITE": "tau" }
    }
  }
}
```

<div dir="rtl">

## 🔌 עובד גם עם אפליקציות אחרות

MCP הוא סטנדרט פתוח, אז מה שקובע זה האפליקציה ולא המודל. כל אפליקציה שמריצה שרתי MCP מקומיים יכולה להשתמש בו, עם GPT, עם Gemini או אפילו עם מודל שרץ אצלכם במחשב. מוסיפים את אותו בלוק JSON משלב 3 לקובץ ההגדרות של האפליקציה:

| אפליקציה | איפה מגדירים |
| --- | --- |
| Cursor | `~/.cursor/mcp.json` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| Gemini CLI | `~/.gemini/settings.json` |
| LM Studio | בתוך האפליקציה: Program ← Install ← Edit mcp.json |
| VS Code (Copilot, מצב agent) | `.vscode/mcp.json` — המפתח הראשי הוא `servers` במקום `mcpServers`, ומוסיפים `"type": "stdio"` |
| Codex CLI | `~/.codex/config.toml` — בפורמט TOML, ראו למטה |

</div>

```toml
[mcp_servers.moodle]
command = "/full/path/to/moodle-mcp/.venv/bin/python"
args = ["/full/path/to/moodle-mcp/moodle_mcp.py"]
env = { MOODLE_SITE = "tau" }
```

<div dir="rtl">

נבדק רק ב-Claude Code; האחרות לפי מה שהן תומכות בו. **לא עובד** ב-ChatGPT, ב-Gemini באתר, ב-claude.ai ובאפליקציות בטלפון: הן מתחברות רק לשרתים שמתארחים באינטרנט, והשרת הזה רץ על המחשב שלכם.

## 🏫 בוחרים מוסד

מגדירים את `MOODLE_SITE` לאחד מהמפתחות הבאים (ברירת המחדל: `tau`).

**אוניברסיטאות**

`tau` תל אביב · `technion` הטכניון · `bgu` בן-גוריון · `ariel` אריאל · `openu` האוניברסיטה הפתוחה

**מכללות להנדסה, עיצוב וכלליות**

`hit` HIT חולון · `sce` סמי שמעון · `braude` בראודה · `azrieli` עזריאלי ירושלים · `jct` המרכז האקדמי לב · `shenkar` שנקר · `bezalel` בצלאל · `jamd` האקדמיה למוסיקה ולמחול · `mta` האקדמית תל אביב-יפו · `colman` המכללה למינהל · `ruppin` רופין · `kinneret` כנרת · `wgalil` גליל מערבי · `achva` אחוה · `zefat` צפת · `schechter` מכון שכטר

**מכללות לחינוך**

`smkb` סמינר הקיבוצים · `levinsky` לוינסקי · `wingate` וינגייט · `beitberl` בית ברל · `oranim` אורנים · `dyellin` דוד ילין · `kaye` קיי · `washington` גבעת ושינגטון · `talpiot` תלפיות · `shaanan` שאנן · `orot` אורות ישראל · `efrata` אפרתה · `hemdat` חמדת · `sakhnin` סכנין · `qsm` אלקאסמי · `arabcol` המכללה האקדמית הערבית לחינוך, חיפה

**המוסד שלכם לא ברשימה?** אצל העברית, בר-אילן, חיפה, רייכמן, אפקה, ספיר, תל-חי ואחרים ה-Moodle יושב מאחורי מסך התחברות או הגנת בוטים, ולכן לא ניתן היה לבדוק את הכתובת אוטומטית. מגדירים במקום זה את `MOODLE_URL` לכתובת ה-Moodle שלכם — ומוזמנים לפתוח PR שמוסיף אותה.

כדאי לדעת:

- כל 37 האתרים נבדקו כאתרי Moodle פעילים (נובמבר 2026), אבל רק תל אביב נבדקה מקצה לקצה עם token אמיתי. האם אפשר להוציא token תלוי בהגדרות של כל מוסד.
- חלק מהמוסדות פותחים Moodle חדש כל שנה (`moodle26…`, `moodle27…`), ולכן הכתובות האלה דורשות עדכון שנתי.

## 🧠 Skill (לא חובה)

בתיקייה `skills/moodle` יש קובץ `SKILL.md` שמלמד את Claude מתי להשתמש בכל כלי: למשל לבדוק דדליינים לפני שהוא בונה תוכנית לימודים, ולעולם לא להגיש כלום. ב-Claude Code מעתיקים אותו כך:

</div>

```bash
mkdir -p ~/.claude/skills && cp -r skills/moodle ~/.claude/skills/
```

<div dir="rtl">

## ⚙️ הגדרות

| משתנה | ברירת מחדל | משמעות |
| --- | --- | --- |
| `MOODLE_SITE` | `tau` | מפתח המוסד מהרשימה למעלה |
| `MOODLE_URL` | – | כתובת של כל Moodle אחר (גובר על `MOODLE_SITE`) |
| `MOODLE_TOKEN` | – | ה-token (גובר על הקובץ `.token`) |
| `MOODLE_TOKEN_FILE` | `.token` ליד הסקריפט | מאיפה לקרוא את ה-token |
| `MOODLE_DOWNLOAD_DIR` | `~/Downloads/moodle` | לאן נשמרות ההורדות |
| `MOODLE_MAX_DOWNLOAD_MB` | `200` | גודל מקסימלי לקובץ מורד (קובץ גדול יותר נעצר ונמחק) |
| `MOODLE_SKIP_PREFLIGHT` | – | `1` מדלג על בדיקת הזהות של האתר (לא מומלץ; ראו אבטחה) |
| `MOODLE_DOWNLOAD_TOKEN_IN_QUERY` | – | `1` שולח את ה-token בכתובת ההורדה במקום בגוף הבקשה. רק למוסד שמסרב לגוף הבקשה |

## 🔒 אבטחה

- ה-token שווה ערך לסיסמה לקריאת ה-Moodle שלכם. לא מעלים אותו ל-git ולא מדביקים אותו בצ'אט.
- ב-`--login` הסיסמה עוברת רק ל-Moodle של המוסד ולא נשמרת. המפתח שחוזר נבדק לפני שהוא נשמר.
- ה-token נשלח רק לשרת ה-Moodle שהוגדר, ורק בגוף הבקשה (POST), לא בכתובת. כך הוא לא נכנס ללוגים ולא להודעות שגיאה. הורדה מכל כתובת אחרת, או מכתובת שאינה `webservice/pluginfile.php`, נחסמת.
- לפני שה-token נשלח בפעם הראשונה השרת שואל את Moodle (בלי token) מי הוא, ומסרב אם האתר לא מזדהה כמוסד שהוגדרת. זה תופס שגיאות הקלדה וכתובות שהוחלפו.
- השרת קורא רק לרשימה סגורה של פונקציות קריאה של Moodle (`READ_FUNCTIONS` בקוד), והבדיקה נעשית לפני כל בקשה. כלי חדש חייב להוסיף את הפונקציה שלו לרשימה, ולכן זה גלוי ב-diff. שימו לב: זה מגביל את **השרת**, לא את ה-token: ל-token עצמו יש ב-Moodle הרשאות רחבות יותר (כמו הגשת מטלות ופרסום בפורום).
- כל טקסט שנכתב על ידי אחרים ב-Moodle (שמות קורסים וקבצים, הודעות בפורומים, תיאורי מטלות, התראות) מוחזר עטוף ב-`<untrusted_moodle_content source="...">`, ומוסרים ממנו טקסט נסתר (HTML מוסתר, תווים בלתי נראים). השרת מורה למודל להתייחס אליו כמידע ולא כהוראות, כדי להקשות על prompt injection (למשל פוסט שמנסה להפעיל כלים). זה מקשה אבל לא מבטל את הסיכון; הרשימה הסגורה למעלה מגבילה את הנזק גם אם המודל בכל זאת יתפתה.
- הכלים בודקים את הפרמטרים לפני כל בקשה (מספרים חיוביים, טווחי ימים, אורך טקסט), ותשובה לא צפויה מ-Moodle נותנת ערך ריק במקום קריסה.
- `python moodle_mcp.py --doctor` בודק את ההתקנה (כתובת, token, זהות האתר) ומציג אילו פונקציות כתיבה ה-token שלכם מחזיק. הוא לא מדפיס את ה-token ולא נתונים אישיים.
- `MOODLE_URL` חייב להתחיל ב-`https://` (`http://` רק ל-localhost), בלי שם משתמש/סיסמה בכתובת.
- הורדות לא יכולות לצאת מתיקיית ההורדות: שמות קבצים ותיקיות מנוקים (גם שמות שמורים של Windows כמו `CON`), יש הגבלת גודל וזמן, ואין מעקב אחרי הפניות.
- הטסטים רצים בלי רשת ובלי token אמיתי: `pip install -r requirements-dev.txt` ואז `python -m pytest`.

</div>

---

<a name="english"></a>

## 🇬🇧 English

**Talk to your Moodle.** A tiny [MCP](https://modelcontextprotocol.io) server that lets Claude read your courses, deadlines, grades and files — so you can ask instead of click. Read-only by design: it never submits, posts or changes anything.

Built at Tel Aviv University, with presets for 37 Israeli universities and colleges — and it works with any other Moodle that hands out a web service token.

### ⚡ The easy way: let Claude install it

Open [Claude Code](https://claude.com/claude-code) and say:

```text
Install https://github.com/raztassa2000-eng/moodle-mcp for me — I study at the Technion
```

Claude clones, installs, picks your institution and connects the server. You only save the token yourself (step 2 below) — never paste it into the chat.

### 🚀 Manual setup

```bash
git clone https://github.com/raztassa2000-eng/moodle-mcp.git
cd moodle-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

**Log in** (replace `tau` with your institution's key):

```bash
.venv/bin/pip install playwright
MOODLE_SITE=tau .venv/bin/python moodle_mcp.py --login
```

It picks your institution's login method by itself: a Chrome/Edge window where you log in as usual (TAU, Technion and 20 others), or your Moodle username and password typed into the terminal (hidden, sent only to your Moodle, never stored). Either way you get your existing key, with no Reset, so the Moodle phone app stays logged in. The key goes into `.token` and is never printed. Only TAU has been tested end to end.

*Didn't work?* In Moodle, profile picture → **Preferences** → **Security keys**, click **Reset** on the *Moodle mobile web service* row (the full key only shows after a reset; this logs the phone app out), copy it and run `pbpaste > .token && chmod 600 .token` on macOS, or `Get-Clipboard | Set-Content .token` in PowerShell on Windows.

**Connect** (replace `tau` with your institution's key from the list above):

```bash
claude mcp add moodle -s user -e MOODLE_SITE=tau -- "$PWD/.venv/bin/python" "$PWD/moodle_mcp.py"
```

For Claude Desktop, add the JSON block shown above to `claude_desktop_config.json` (Settings → Developer → Edit Config; on macOS it lives in `~/Library/Application Support/Claude/`, on Windows in `%APPDATA%\Claude\`) and restart the app. The server runs on your own computer, so it works in Claude Code and Claude Desktop but not on claude.ai in the browser or in the phone app.

**Tools:** `whats_new` (everything that changed across your courses, in one call), `list_courses`, `course_contents`, `upcoming_deadlines`, `calendar_events`, `assignments` (with grade and grader feedback), `quizzes`, `grades`, `grades_overview`, `forum_discussions`, `forum_thread`, `notifications`, `download_file` and `download_course` (a whole course into one folder, skipping files you already have).

Run `.venv/bin/python moodle_mcp.py --sites` for the full list of institutions with names and URLs; for anything not listed, set `MOODLE_URL`.

**Other apps:** MCP is an open standard, so any app that runs local MCP servers works, whatever model it uses. Add the same JSON block to Cursor (`~/.cursor/mcp.json`), Windsurf (`~/.codeium/windsurf/mcp_config.json`), Gemini CLI (`~/.gemini/settings.json`) or LM Studio (Program → Install → Edit mcp.json). VS Code uses `.vscode/mcp.json` with a top-level `servers` key and `"type": "stdio"`; Codex CLI uses the TOML block above in `~/.codex/config.toml`. Only Claude Code has been tested. It does not work in ChatGPT, Gemini on the web, claude.ai or phone apps, which only connect to servers hosted on the internet.

All 37 sites were checked to be live Moodle sites (November 2026), but only TAU has been tested end to end with a real token.

### Security and settings

- Your token is as sensitive as a password for reading your Moodle. Don't commit it or paste it into a chat.
- The token is sent only to the configured Moodle host, and only in the POST body, never in a URL, so it can't end up in logs or error messages. Downloads from any other host, or from anything but `webservice/pluginfile.php`, are refused.
- Before the token is first sent, the server asks the site (without the token) who it is, and refuses if it doesn't identify itself as the host you configured. That catches typos and hosts that changed hands.
- The server only calls a closed list of read-only Moodle functions (`READ_FUNCTIONS` in the code), checked before every request. A new tool has to add its function to that list, so it shows up in the diff. Note that this limits the **server**, not your token: the token itself can do more in Moodle (submit assignments, post in forums).
- Text written by other people in Moodle (course and file names, forum posts, assignment descriptions, notifications) is returned wrapped in `<untrusted_moodle_content source="...">`, with hidden content (hidden HTML, invisible characters) removed. The server tells the assistant to treat it as data, not instructions, which makes prompt injection harder (for example a post that tries to make the assistant call tools). That reduces the risk but does not remove it; the closed list above limits the damage even if the assistant is tempted.
- Tools validate their arguments before any request (positive ids, day ranges, text length), and an unexpected reply from Moodle yields an empty value instead of a crash.
- `python moodle_mcp.py --doctor` checks your setup (URL, token, site identity) and lists the write functions your token holds. It prints no token and no personal data.
- `MOODLE_URL` must be `https://` (`http://` only for localhost) and contain no user name or password.
- Downloads can't leave the download folder: file and folder names are sanitized (including Windows device names like `CON`), there is a size and time limit, and redirects are never followed.
- Tests run offline with no real token: `pip install -r requirements-dev.txt && python -m pytest`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `MOODLE_SITE` | `tau` | institution key (`--sites`) |
| `MOODLE_URL` | – | any other Moodle (overrides `MOODLE_SITE`) |
| `MOODLE_TOKEN` / `MOODLE_TOKEN_FILE` | `.token` next to the script | where the token comes from |
| `MOODLE_DOWNLOAD_DIR` | `~/Downloads/moodle` | where downloads go |
| `MOODLE_MAX_DOWNLOAD_MB` | `200` | largest file a download may be |
| `MOODLE_SKIP_PREFLIGHT` | – | `1` skips the site identity check (not recommended) |
| `MOODLE_DOWNLOAD_TOKEN_IN_QUERY` | – | `1` puts the token in the download URL instead of the request body; only for a Moodle that rejects the body |

---

## 🤖 Instructions for Claude (automatic install)

If a user gave you this repository and asked you to install it, do this:

1. Clone the repo to a sensible permanent location (ask if unsure), create `.venv`, and `pip install -r requirements.txt`. Python 3.10+ is required.
2. Map the institution the user named to a key: run `.venv/bin/python moodle_mcp.py --sites`. If it isn't listed, ask the user for their Moodle address and use `MOODLE_URL` instead of `MOODLE_SITE`.
3. Register the server with absolute paths:
   `claude mcp add moodle -s user -e MOODLE_SITE=<key> -- <repo>/.venv/bin/python <repo>/moodle_mcp.py`
   If Claude Desktop is installed (its `claude_desktop_config.json` exists: `~/Library/Application Support/Claude/` on macOS, `%APPDATA%\Claude\` on Windows), also add the server there: back the file up, then add a `moodle` entry under `mcpServers` with the same `command`, `args` and `env`, keeping every other key untouched. Tell the user to restart Claude Desktop.
4. **Do not ask for the token or password in chat and do not read `.token`.** Install `playwright` into the venv, then ask the user to run this in their own terminal (it may open a browser window or prompt for a hidden password): `cd <repo> && MOODLE_SITE=<key> .venv/bin/python moodle_mcp.py --login`. If that fails, the fallback is manual: Moodle → Preferences → Security keys → **Reset** on the *Moodle mobile web service* row, copy, and `pbpaste > <repo>/.token && chmod 600 <repo>/.token` (on Windows/Linux, save it to that file with their own editor).
5. Install the skill: `mkdir -p ~/.claude/skills && cp -r <repo>/skills/moodle ~/.claude/skills/`.
6. After they confirm, verify with `MOODLE_SITE=<key> .venv/bin/python -c "import moodle_mcp as m; print(len(m.list_courses()), 'courses')"`, then tell them to start a new session so the tools load.
7. If verification fails with `invalidtoken` or a web-service error, say so plainly: their institution may not issue tokens to students. Do not try to obtain a token another way.

---

<div dir="rtl">

עזר לכם? תנו ⭐ לריפו כדי שעוד סטודנטים ימצאו אותו.

</div>

If this helped you, a ⭐ helps other students find it.

[MIT License](LICENSE) · Not affiliated with any university, college or Moodle.
