"""Personal dashboard: to-do list, habit tracker and book tracker (Streamlit + Supabase Postgres)."""
import hmac
from datetime import date, timedelta

import psycopg
import streamlit as st
from psycopg.rows import dict_row

GRID_DAYS = 30  # number of days shown in each habit's green-block row

st.set_page_config(page_title="Dashboard", layout="wide")


# ---------- login ----------
def require_login():
    """Block the page until the shared password is entered, if one is set in the secrets.

    With no [app] password in the secrets, the dashboard opens straight away.
    """
    expected = st.secrets.get("app", {}).get("password")
    if not expected or st.session_state.get("authed"):
        return
    st.title("Dashboard")
    entered = st.text_input("Password", type="password")
    if entered:
        # compare_digest avoids leaking information through comparison timing
        if hmac.compare_digest(entered.encode(), expected.encode()):
            st.session_state["authed"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


# ---------- database ----------
@st.cache_resource
def get_conn():
    """One shared connection, kept alive across reruns instead of reconnecting on every click."""
    return psycopg.connect(
        st.secrets["db"]["url"],
        autocommit=True,        # each statement commits immediately
        row_factory=dict_row,   # rows behave like dicts, e.g. row["title"]
        prepare_threshold=None, # no server-side prepared statements (safe behind a pooler)
    )


def run(sql, params=(), fetch=False):
    """Execute one statement. Reconnects once if the cached connection has gone stale."""
    for attempt in (1, 2):
        try:
            with get_conn().cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() if fetch else None
        except (psycopg.OperationalError, psycopg.InterfaceError):
            get_conn.clear()  # drop the dead connection; the retry opens a new one
            if attempt == 2:
                raise


def query(sql, params=()):
    """Run a SELECT and return all rows. Always pass values via `params`, never f-strings."""
    return run(sql, params, fetch=True)


def execute(sql, params=()):
    """Run an INSERT, UPDATE or DELETE."""
    run(sql, params)


# ---------- habit helpers ----------
def streak(done_days: set, today: date) -> int:
    """Consecutive successful days ending today (or yesterday if today is not yet ticked)."""
    # Start from yesterday if today is not ticked yet, so the streak survives until midnight
    d = today if today in done_days else today - timedelta(days=1)
    n = 0
    # Walk backwards one day at a time until we hit a missed day
    while d in done_days:
        n += 1
        d -= timedelta(days=1)
    return n


def grid_html(done_days: set, today: date) -> str:
    """Build one row of small squares, oldest day on the left and today on the right."""
    cells = []
    for i in range(GRID_DAYS - 1, -1, -1):
        d = today - timedelta(days=i)
        # green = done, dark grey = missed; the title gives a hover tooltip with the date
        colour = "#2ea043" if d in done_days else "#30363d"
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
            execute("INSERT INTO todos (title, due) VALUES (%s, %s)", (title.strip(), due))
            st.rerun()

    # Open tasks first, soonest due date first, undated last.
    # `due IS NULL` is false for dated tasks and true for undated ones, which pushes the latter down.
    rows = query("SELECT * FROM todos ORDER BY done, due IS NULL, due, id")
    today = date.today()
    for r in rows:
        c1, c2 = st.columns([8, 1])
        label = r["title"]
        if r["due"]:
            tag = f" (due {r['due']})"
            if not r["done"] and r["due"] < today:
                tag = f" :red[(overdue, {r['due']})]"  # Streamlit markdown colour syntax
            label += tag
        if r["done"]:
            label = f"~~{label}~~"  # strikethrough for completed tasks
        # The key must be unique per task so Streamlit can track each checkbox
        checked = c1.checkbox(label, value=r["done"], key=f"todo_{r['id']}")
        # Only write to the database when the box was actually toggled
        if checked != r["done"]:
            execute("UPDATE todos SET done = %s WHERE id = %s", (checked, r["id"]))
            st.rerun()
        if c2.button("✕", key=f"del_todo_{r['id']}", help="Delete"):
            execute("DELETE FROM todos WHERE id = %s", (r["id"],))
            st.rerun()

    if any(r["done"] for r in rows) and st.button("Clear completed"):
        execute("DELETE FROM todos WHERE done")
        st.rerun()


def habit_column():
    st.subheader("Habits")
    today = date.today()

    habits = query("SELECT * FROM habits ORDER BY category, name")
    # Map each habit id to the set of dates it was completed, e.g. {1: {date(2026, 10, 10), ...}}
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
        done_today = today in done_days

        c1, c2 = st.columns([5, 2])
        ticked = c1.checkbox(h["name"], value=done_today, key=f"habit_{h['id']}")
        c2.markdown(f"🔥 **{streak(done_days, today)}**")
        # Ticking inserts a log row for today; unticking removes it.
        # ON CONFLICT DO NOTHING avoids an error if the row already exists.
        if ticked != done_today:
            if ticked:
                execute(
                    "INSERT INTO habit_log (habit_id, day) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (h["id"], today),
                )
            else:
                execute(
                    "DELETE FROM habit_log WHERE habit_id = %s AND day = %s",
                    (h["id"], today),
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
                        "INSERT INTO habits (name, category) VALUES (%s, %s)",
                        (name.strip(), category),
                    )
                    st.rerun()
                except psycopg.errors.UniqueViolation:
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
                    "INSERT INTO habit_log (habit_id, day) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (names[pick], day),
                )
                st.rerun()
            if b2.button("Clear day"):
                execute(
                    "DELETE FROM habit_log WHERE habit_id = %s AND day = %s",
                    (names[pick], day),
                )
                st.rerun()

            st.markdown("**Delete a habit**")
            gone = st.selectbox("Habit to delete", list(names), key="delete_habit")
            if st.button("Delete habit and its history"):
                # ON DELETE CASCADE removes the matching habit_log rows automatically
                execute("DELETE FROM habits WHERE id = %s", (names[gone],))
                st.rerun()


def book_column():
    st.subheader("Reading")
    today = date.today()

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
                        "UPDATE books SET current_page = %s, status = 'finished', finished = %s "
                        "WHERE id = %s",
                        (b["total_pages"], today, b["id"]),
                    )
                else:
                    execute("UPDATE books SET current_page = %s WHERE id = %s", (page, b["id"]))
                st.rerun()
        if st.button("Remove", key=f"del_book_{b['id']}"):
            execute("DELETE FROM books WHERE id = %s", (b["id"],))
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
                    "VALUES (%s, %s, %s, %s, %s)",
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
require_login()

# Accent colour per column. Each panel is a bordered container with a fixed key,
# which Streamlit exposes as the CSS class `st-key-<key>`.
COLOURS = {"panel_todo": "#ff4b4b", "panel_habit": "#00e5ff", "panel_book": "#3fb950"}
BACKGROUND = "#301934"  # "Dark Purple"
st.markdown(
    f"<style>.stApp {{ background-color: {BACKGROUND}; }}"
    + "".join(
        f".st-key-{key} {{ border: 2px solid {c} !important; }}"
        f".st-key-{key} h3 {{ color: {c} !important; }}"
        for key, c in COLOURS.items()
    )
    + "</style>",
    unsafe_allow_html=True,
)

# A plain <h1> instead of st.title so it can be centred
st.markdown("<h1 style='text-align: center;'>Dashboard</h1>", unsafe_allow_html=True)
# Relative widths; the habit column is wider to fit the 30-day grid
col_todo, col_habit, col_skill = st.columns([1, 1.4, 1])
with col_todo, st.container(border=True, key="panel_todo"):
    todo_column()
with col_habit, st.container(border=True, key="panel_habit"):
    habit_column()
with col_skill, st.container(border=True, key="panel_book"):
    book_column()
