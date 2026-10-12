"""Personal dashboard trial: to-do list and habit tracker (Streamlit + SQLite)."""
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import streamlit as st

DB_PATH = Path(__file__).parent / "dashboard.db"
GRID_DAYS = 30  # number of days shown in each habit's green-block row

st.set_page_config(page_title="Dashboard", layout="wide")


# ---------- database ----------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # lets us read columns by name, e.g. row["title"]
    # SQLite ignores foreign keys unless enabled per connection; needed for ON DELETE CASCADE
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create the tables on first run; does nothing if they already exist."""
    # `with conn` commits on success and rolls back on error
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                title   TEXT NOT NULL,
                due     TEXT,                      -- ISO date (YYYY-MM-DD) or NULL
                done    INTEGER NOT NULL DEFAULT 0 -- SQLite has no bool: 0 = open, 1 = done
            );
            CREATE TABLE IF NOT EXISTS habits (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL DEFAULT 'Skill'
            );
            -- One row per habit per successful day. A missing row means a missed day.
            -- The composite primary key stops a day being logged twice.
            CREATE TABLE IF NOT EXISTS habit_log (
                habit_id INTEGER NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
                day      TEXT NOT NULL,
                PRIMARY KEY (habit_id, day)
            );
            -- status is 'reading' or 'finished'; dates are ISO strings
            CREATE TABLE IF NOT EXISTS books (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT NOT NULL,
                author       TEXT,
                total_pages  INTEGER NOT NULL CHECK (total_pages > 0),
                current_page INTEGER NOT NULL DEFAULT 0,
                status       TEXT NOT NULL DEFAULT 'reading',
                started      TEXT,
                finished     TEXT
            );
            """
        )


def query(sql, params=()):
    """Run a SELECT and return all rows. Always pass values via `params`, never f-strings."""
    with get_conn() as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql, params=()):
    """Run an INSERT, UPDATE or DELETE and commit it."""
    with get_conn() as conn:
        conn.execute(sql, params)


# ---------- habit helpers ----------
def streak(done_days: set, today: date) -> int:
    """Consecutive successful days ending today (or yesterday if today is not yet ticked)."""
    # Start from yesterday if today is not ticked yet, so the streak survives until midnight
    d = today if today.isoformat() in done_days else today - timedelta(days=1)
    n = 0
    # Walk backwards one day at a time until we hit a missed day
    while d.isoformat() in done_days:
        n += 1
        d -= timedelta(days=1)
    return n


def grid_html(done_days: set, today: date) -> str:
    """Build one row of small squares, oldest day on the left and today on the right."""
    cells = []
    for i in range(GRID_DAYS - 1, -1, -1):
        d = today - timedelta(days=i)
        # green = done, dark grey = missed; the title gives a hover tooltip with the date
        colour = "#2ea043" if d.isoformat() in done_days else "#30363d"
        cells.append(
            f'<span title="{d:%a %d %b}" style="display:inline-block;width:14px;'
            f'height:14px;margin:1px;border-radius:3px;background:{colour}"></span>'
        )
    return "".join(cells)


# ---------- columns ----------
def todo_column():
    st.subheader("To-do")

    # A form batches the inputs so the app only reruns when "Add" is pressed
    with st.form("add_todo", clear_on_submit=True):
        title = st.text_input("New task", label_visibility="collapsed", placeholder="New task")
        c1, c2 = st.columns([3, 1])
        due = c1.date_input("Due (optional)", value=None, format="YYYY-MM-DD")
        submitted = c2.form_submit_button("Add", use_container_width=True)
        if submitted and title.strip():
            execute(
                "INSERT INTO todos (title, due) VALUES (?, ?)",
                (title.strip(), due.isoformat() if due else None),
            )
            st.rerun()

    # Open tasks first, soonest due date first, undated last.
    # `due IS NULL` is 0 for dated tasks and 1 for undated ones, which pushes the latter down.
    rows = query(
        "SELECT * FROM todos ORDER BY done, due IS NULL, due, id"
    )
    today = date.today().isoformat()
    for r in rows:
        c1, c2 = st.columns([8, 1])
        label = r["title"]
        if r["due"]:
            tag = f" (due {r['due']})"
            # ISO dates compare correctly as plain strings
            if not r["done"] and r["due"] < today:
                tag = f" :red[(overdue, {r['due']})]"  # Streamlit markdown colour syntax
            label += tag
        if r["done"]:
            label = f"~~{label}~~"  # strikethrough for completed tasks
        # The key must be unique per task so Streamlit can track each checkbox
        checked = c1.checkbox(label, value=bool(r["done"]), key=f"todo_{r['id']}")
        # Only write to the database when the box was actually toggled
        if checked != bool(r["done"]):
            execute("UPDATE todos SET done = ? WHERE id = ?", (int(checked), r["id"]))
            st.rerun()
        if c2.button("✕", key=f"del_todo_{r['id']}", help="Delete"):
            execute("DELETE FROM todos WHERE id = ?", (r["id"],))
            st.rerun()

    if any(r["done"] for r in rows) and st.button("Clear completed"):
        execute("DELETE FROM todos WHERE done = 1")
        st.rerun()


def habit_column():
    st.subheader("Habits")
    today = date.today()

    habits = query("SELECT * FROM habits ORDER BY category, name")
    # Map each habit id to the set of ISO dates it was completed, e.g. {1: {"2026-10-10", ...}}
    log = {}
    for r in query("SELECT habit_id, day FROM habit_log"):
        log.setdefault(r["habit_id"], set()).add(r["day"])

    if not habits:
        st.info("No habits yet. Add one below.")

    # Habits are sorted by category, so print a heading whenever the category changes
    current_category = None
    for h in habits:
        if h["category"] != current_category:
            current_category = h["category"]
            st.caption(current_category.upper())
        done_days = log.get(h["id"], set())
        done_today = today.isoformat() in done_days

        c1, c2 = st.columns([5, 2])
        ticked = c1.checkbox(h["name"], value=done_today, key=f"habit_{h['id']}")
        c2.markdown(f"🔥 **{streak(done_days, today)}**")
        # Ticking inserts a log row for today; unticking removes it.
        # INSERT OR IGNORE avoids an error if the row already exists.
        if ticked != done_today:
            if ticked:
                execute(
                    "INSERT OR IGNORE INTO habit_log (habit_id, day) VALUES (?, ?)",
                    (h["id"], today.isoformat()),
                )
            else:
                execute(
                    "DELETE FROM habit_log WHERE habit_id = ? AND day = ?",
                    (h["id"], today.isoformat()),
                )
            st.rerun()
        # unsafe_allow_html is needed for the coloured squares; the HTML is built only
        # from dates we generate ourselves, never from user input
        st.markdown(grid_html(done_days, today), unsafe_allow_html=True)

    with st.expander("Add or edit habits"):
        with st.form("add_habit", clear_on_submit=True):
            name = st.text_input("Habit name")
            category = st.selectbox("Category", ["Skill", "Diet", "Fitness", "Other"])
            if st.form_submit_button("Add habit") and name.strip():
                try:
                    execute(
                        "INSERT INTO habits (name, category) VALUES (?, ?)",
                        (name.strip(), category),
                    )
                    st.rerun()
                except sqlite3.IntegrityError:
                    # raised by the UNIQUE constraint on habits.name
                    st.warning("That habit already exists.")

        if habits:
            # Forgot to tick something yesterday? Set any past day here.
            st.markdown("**Backfill a past day**")
            names = {h["name"]: h["id"] for h in habits}  # name -> id, for the dropdowns
            pick = st.selectbox("Habit", list(names), key="backfill_habit")
            day = st.date_input(
                "Day", value=today, max_value=today,
                min_value=today - timedelta(days=365), key="backfill_day",
            )
            b1, b2 = st.columns(2)
            if b1.button("Mark done"):
                execute(
                    "INSERT OR IGNORE INTO habit_log (habit_id, day) VALUES (?, ?)",
                    (names[pick], day.isoformat()),
                )
                st.rerun()
            if b2.button("Clear day"):
                execute(
                    "DELETE FROM habit_log WHERE habit_id = ? AND day = ?",
                    (names[pick], day.isoformat()),
                )
                st.rerun()

            st.markdown("**Delete a habit**")
            gone = st.selectbox("Habit to delete", list(names), key="delete_habit")
            if st.button("Delete habit and its history"):
                # ON DELETE CASCADE removes the matching habit_log rows automatically
                execute("DELETE FROM habits WHERE id = ?", (names[gone],))
                st.rerun()


def book_column():
    st.subheader("Reading")
    today = date.today().isoformat()

    reading = query("SELECT * FROM books WHERE status = 'reading' ORDER BY id")
    if not reading:
        st.info("Not reading anything. Add a book below.")

    for b in reading:
        title = b["title"] + (f" by {b['author']}" if b["author"] else "")
        st.markdown(f"**{title}**")
        pct = b["current_page"] / b["total_pages"]
        st.progress(pct, text=f"Page {b['current_page']} of {b['total_pages']} ({pct:.0%})")

        # A form per book so the number box only saves when "Update" is pressed
        with st.form(f"update_book_{b['id']}", border=False):
            c1, c2 = st.columns([2, 1])
            page = c1.number_input(
                "Current page", min_value=0, max_value=b["total_pages"],
                value=b["current_page"], step=1, label_visibility="collapsed",
            )
            if c2.form_submit_button("Update", use_container_width=True):
                # Reaching the last page marks the book as finished automatically
                if page >= b["total_pages"]:
                    execute(
                        "UPDATE books SET current_page = ?, status = 'finished', finished = ? "
                        "WHERE id = ?",
                        (b["total_pages"], today, b["id"]),
                    )
                else:
                    execute("UPDATE books SET current_page = ? WHERE id = ?", (page, b["id"]))
                st.rerun()
        if st.button("Remove", key=f"del_book_{b['id']}"):
            execute("DELETE FROM books WHERE id = ?", (b["id"],))
            st.rerun()

    with st.expander("Add a book"):
        with st.form("add_book", clear_on_submit=True):
            title = st.text_input("Title")
            author = st.text_input("Author (optional)")
            c1, c2 = st.columns(2)
            total = c1.number_input("Total pages", min_value=1, value=300, step=1)
            start = c2.number_input("Starting page", min_value=0, value=0, step=1)
            if st.form_submit_button("Add book") and title.strip():
                # Clamp so the starting page can never exceed the book's length
                execute(
                    "INSERT INTO books (title, author, total_pages, current_page, started) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (title.strip(), author.strip() or None, total, min(start, total - 1), today),
                )
                st.rerun()

    finished = query("SELECT * FROM books WHERE status = 'finished' ORDER BY finished DESC, id DESC")
    if finished:
        with st.expander(f"Finished ({len(finished)})"):
            for b in finished:
                st.write(f"{b['title']}, {b['total_pages']} pages, finished {b['finished']}")


# ---------- page ----------
# Streamlit reruns this whole script on every interaction, so everything that must
# persist lives in the database rather than in variables.
init_db()

# Accent colour per column. Each panel is a bordered container with a fixed key,
# which Streamlit exposes as the CSS class `st-key-<key>`.
COLOURS = {"panel_todo": "#ff4b4b", "panel_habit": "#00e5ff", "panel_skill": "#ffffff"}
st.markdown(
    "<style>"
    + "".join(
        f".st-key-{key} {{ border: 2px solid {c} !important; }}"
        f".st-key-{key} h3 {{ color: {c} !important; }}"
        for key, c in COLOURS.items()
    )
    + "</style>",
    unsafe_allow_html=True,
)

st.title("Dashboard")
# Relative widths; the habit column is wider to fit the 30-day grid
col_todo, col_habit, col_skill = st.columns([1, 1.4, 1])
with col_todo, st.container(border=True, key="panel_todo"):
    todo_column()
with col_habit, st.container(border=True, key="panel_habit"):
    habit_column()
with col_skill, st.container(border=True, key="panel_skill"):
    book_column()
