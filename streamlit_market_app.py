"""Chat-first UI for the Finance AI market + PDF assistant.

No ticker or exchange textboxes: the API searches real listings, then the user
chooses the exact exchange in the conversation only when necessary.
Run: python -m streamlit run streamlit_market_app.py
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
import os
import re

import altair as alt
import httpx
import pandas as pd
import streamlit as st

API_URL = os.getenv("FINANCE_AI_API_URL", "http://127.0.0.1:8000").rstrip("/")
ACCESS_TOKEN = os.getenv("FINANCE_AI_MARKET_ACCESS_TOKEN", "")
HEADERS = {"X-Market-Token": ACCESS_TOKEN} if ACCESS_TOKEN else {}
PERIOD_ONLY_FOLLOWUP = re.compile(
    r"^\s*(?:(?:please\s+)?(?:use|choose|select|go with|take)\s+)?"
    r"(?:(?:and|what about|how about)\s+(?:in\s+)?)?"
    r"(?:the\s+)?Q([1-4])(?:\s*(20\d{2}|\d{2}))?(?:\s+report)?[.!?]*\s*$", re.I
)
SHORT_SLOT_REPLY = re.compile(
    r"^(?:commission income|net commission income|net fee and commission income|"
    r"net interest income|total operating income|operating income|net profit|"
    r"Nordea|SEB|Handelsbanken)\s*[.!?]*$", re.I
)

st.set_page_config(page_title="Financial Intelligence", page_icon="💬", layout="centered")
st.markdown("""
<style>
  .block-container {max-width: 940px; padding-top: 2rem; padding-bottom: 6rem;}
  [data-testid="stChatMessage"] {border-radius: 14px;}
  [data-testid="stChatInput"] {max-width: 920px;}
  div[data-testid="stSidebar"] .stCaption {opacity: 0.8;}
  h1 {font-size: 1.85rem !important; letter-spacing: -0.025em;}
</style>
""", unsafe_allow_html=True)


def request(method: str, path: str, **kwargs) -> dict:
    """The Streamlit Python server talks to FastAPI; no provider key in browser."""
    try:
        with httpx.Client(timeout=httpx.Timeout(200.0, connect=5.0), follow_redirects=False) as client:
            response = client.request(method, API_URL + path, headers=HEADERS, **kwargs)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("invalid_response")
        return data
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 403:
            raise RuntimeError("The API denied access. Check the local server's market-access settings.") from None
        if status == 404:
            raise RuntimeError("The chat route isn't running. Start the updated API with app.api_day13_5:app or app.api_day13_6:app.") from None
        if status == 422:
            raise RuntimeError("That request needs a valid question or an exact stock listing.") from None
        raise RuntimeError(f"The API couldn't complete this request (HTTP {status}).") from None
    except httpx.TimeoutException:
        raise RuntimeError("The answer timed out. Check whether the local LLM/API is still running.") from None
    except (httpx.HTTPError, ValueError):
        raise RuntimeError("The local financial API is unavailable. Start FastAPI and try again.") from None


REPORT_TYPES = {"Interim report": "interim_report", "Factbook": "factbook",
                "Other financial report": "financial_report"}


def upload_report(file, *, issuer: str, year: int, quarter: int, currency: str, report_type: str) -> dict:
    """Send one PDF + metadata to POST /documents/ingest and surface the API's own error text."""
    try:
        with httpx.Client(timeout=httpx.Timeout(900.0, connect=5.0), follow_redirects=False) as client:
            response = client.post(
                API_URL + "/documents/ingest", headers=HEADERS,
                data={"issuer": issuer, "year": year, "quarter": quarter,
                      "currency": currency, "report_type": report_type},
                files={"file": (file.name, file.getvalue(), "application/pdf")},
            )
    except httpx.TimeoutException:
        raise RuntimeError("Indexing is taking longer than expected. Check the API window; if it finishes, "
                           "the report will appear in the list after you refresh the page.") from None
    except httpx.HTTPError:
        raise RuntimeError("The local financial API is unavailable. Start FastAPI and try again.") from None
    if response.status_code == 200:
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("document"), dict):
            return data
        raise RuntimeError("The API returned an unexpected response.")
    if response.status_code in (404, 405):
        raise RuntimeError("The running API has no upload route. Restart it with app.api_day13_6:app.")
    if response.status_code == 403:
        raise RuntimeError("The API denied access. Check the local server's access settings.")
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, str) and detail:
        raise RuntimeError(detail)
    if response.status_code == 422:
        raise RuntimeError("One of the metadata fields is invalid. Check issuer, year, quarter and currency.")
    raise RuntimeError(f"The API couldn't index this report (HTTP {response.status_code}).")


def delete_report(document_id: str) -> dict:
    """Call the API's single-document deletion route."""
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0), follow_redirects=False) as client:
            response = client.delete(API_URL + f"/documents/{quote(document_id, safe='')}", headers=HEADERS)
    except httpx.HTTPError:
        raise RuntimeError("The local financial API is unavailable. Start FastAPI and try again.") from None
    if response.status_code == 200:
        result = response.json()
        if isinstance(result, dict):
            return result
        raise RuntimeError("The API returned an unexpected deletion response.")
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    if response.status_code == 404:
        if isinstance(detail, str) and "not present" in detail.casefold():
            raise RuntimeError(detail)
        raise RuntimeError("The running API does not support report deletion yet. Restart it with app.api_day13_6:app.")
    if response.status_code == 403:
        raise RuntimeError("The API denied access. Check the local server's access settings.")
    if isinstance(detail, str) and detail:
        raise RuntimeError(detail)
    raise RuntimeError(f"The API couldn't delete this report (HTTP {response.status_code}).")


def manage_documents_section(docs: list[dict]) -> None:
    """Delete one selected report only after an explicit confirmation."""
    reset_confirmation_for = st.session_state.pop("reset_delete_confirmation_for", None)
    if reset_confirmation_for:
        st.session_state.pop(f"confirm_delete_{reset_confirmation_for}", None)
    notice = st.session_state.pop("delete_notice", None)
    if notice:
        st.success(f"Deleted **{notice}** from the local report index.")
    with st.expander("Manage indexed reports"):
        if not docs:
            st.caption("No indexed reports to manage yet.")
            return
        by_id = {d["document_id"]: d for d in docs}
        options = list(by_id)
        if st.session_state.get("manage_report_id") not in options:
            st.session_state["manage_report_id"] = options[0]
        selected = st.selectbox(
            "Select a report to delete", options, key="manage_report_id",
            format_func=lambda value: f"{label_for(by_id[value])} · {value[:8]}")
        document = by_id[selected]
        st.caption(f"File: {document.get('filename', 'unknown')} · "
                   f"{document.get('chunk_count', 0)} indexed chunks")
        confirmed = st.checkbox(
            "I understand this permanently removes the selected report from the local index.",
            key=f"confirm_delete_{selected}")
        if st.button("Delete selected report", key=f"delete_report_{selected}",
                      use_container_width=True, type="secondary"):
            if not confirmed:
                st.error("Confirm deletion before continuing.")
                return
            try:
                delete_report(selected)
            except RuntimeError as exc:
                st.error(str(exc))
                return
            get_documents.clear()
            st.session_state["reset_delete_confirmation_for"] = selected
            st.session_state["delete_notice"] = label_for(document)
            st.rerun()


def upload_section() -> None:
    """Sidebar form: choose a PDF, confirm its metadata, upload and index it."""
    nonce = st.session_state.setdefault("upload_nonce", 0)  # bumping it resets the form after success
    notice = st.session_state.pop("upload_notice", None)
    with st.expander("Add a financial report", expanded=notice is not None):
        if notice:
            getattr(st, notice[0])(notice[1])
        with st.form(f"upload_form_{nonce}", border=False):
            file = st.file_uploader("Choose a PDF file", type=["pdf"], key=f"upload_file_{nonce}",
                                    help="One text-based PDF. Scanned (image-only) PDFs are not supported yet.")
            issuer = st.text_input("Issuer", placeholder="e.g. Jyske Bank", key=f"upload_issuer_{nonce}")
            col_year, col_quarter = st.columns(2)
            year = col_year.number_input("Year", min_value=2000, max_value=2100, value=date.today().year,
                                         step=1, key=f"upload_year_{nonce}")
            quarter = col_quarter.selectbox("Quarter", [1, 2, 3, 4], format_func=lambda q: f"Q{q}",
                                            key=f"upload_quarter_{nonce}")
            col_cur, col_type = st.columns(2)
            currency = col_cur.text_input("Currency", max_chars=3, placeholder="EUR", key=f"upload_currency_{nonce}")
            type_label = col_type.selectbox("Report type", list(REPORT_TYPES), key=f"upload_type_{nonce}")
            confirmed = st.checkbox("I checked that the issuer, period, currency and report type match this PDF.",
                                    key=f"upload_confirm_{nonce}")
            submitted = st.form_submit_button("Upload and index document", use_container_width=True)
        if not submitted:
            return
        problems = []
        if file is None:
            problems.append("Choose a PDF file.")
        if not issuer.strip():
            problems.append("Enter the issuer.")
        if not re.fullmatch(r"[A-Za-z]{3}", currency.strip()):
            problems.append("Enter a three-letter currency code, for example EUR.")
        if not confirmed:
            problems.append("Confirm that the metadata matches the PDF.")
        if problems:
            st.error(" ".join(problems))
            return
        with st.spinner("Validating and indexing. Large reports can take a few minutes..."):
            try:
                result = upload_report(file, issuer=issuer.strip(), year=int(year), quarter=int(quarter),
                                       currency=currency.strip().upper(), report_type=REPORT_TYPES[type_label])
            except RuntimeError as exc:
                st.error(str(exc))
                return
        doc = result["document"]
        name = doc.get("display_name") or doc.get("filename") or "the report"
        get_documents.clear()  # make the new report appear in the list right away
        st.session_state["pending_report_selection"] = doc.get("document_id")
        st.session_state["upload_nonce"] = nonce + 1
        if result.get("status") == "already_indexed":
            st.session_state["upload_notice"] = (
                "info", f"This exact PDF was already indexed as **{name}**. Nothing new was added. "
                        "It is selected below as the report source.")
        else:
            st.session_state["upload_notice"] = (
                "success", f"Indexed **{name}** ({doc.get('chunk_count', '?')} text chunks). "
                          "It is selected below as the report source.")
        st.rerun()


@st.cache_data(ttl=30, show_spinner=False)
def get_documents() -> list[dict]:
    docs = request("GET", "/documents").get("documents", [])
    return docs if isinstance(docs, list) else []


@st.cache_data(ttl=30, show_spinner=False)
def get_status() -> dict:
    return request("GET", "/market/status")


def label_for(doc: dict) -> str:
    return doc.get("display_name") or doc.get("filename") or "Financial report"


def selection_sidebar(docs: list[dict]) -> dict:
    with st.sidebar:
        st.title("Financial AI")
        st.caption("Reports and stock prices in one conversation")
        st.divider()
        # A freshly uploaded report is pre-selected, but only here, before the widget exists.
        pending = st.session_state.pop("pending_report_selection", None)
        by_id = {d["document_id"]: d for d in docs}
        if pending in by_id:
            st.session_state["chat_report_source"] = pending
        with st.expander("Report source · optional", expanded=pending in by_id):
            options = [None] + list(by_id)
            if st.session_state.get("chat_report_source") not in options:
                st.session_state["chat_report_source"] = None  # selected report no longer exists
            chosen = st.selectbox("Evidence scope", options, key="chat_report_source",
                                  format_func=lambda v: "Automatic (recommended)" if v is None else f"{label_for(by_id[v])} · {v[:8]}")
            if chosen:
                st.caption("Answers will only use this report, even if the question names another bank. "
                           "Choose Automatic to go back to normal selection.")
                selection = {"mode": "manual", "document_ids": [chosen]}
            else:
                st.caption("Reports are selected from the issuer and reporting period in your question.")
                selection = {"mode": "auto", "document_ids": []}
        upload_section()
        manage_documents_section(docs)
        st.divider()
        try:
            status = get_status()
            if status.get("configured"):
                plan = (status.get("subscription") or {}).get("configured_plan", "basic").upper()
                st.caption(f"🟢 Twelve Data connected · {plan} plan setting")
                st.caption("The plan label is set by TWELVE_DATA_PLAN; the API key's commercial tier is not auto-detected.")
                if plan == "BASIC":
                    st.caption("Basic coverage in this app: default US listed-equity feed (for example, AAPL and MSFT).")
                st.caption("Nordic primary exchanges require Pro. OTC instruments need a separate data licence.")
            else:
                plan = (status.get("subscription") or {}).get("configured_plan", "basic").upper()
                st.caption(f"⚪ Market quotes not configured · {plan} plan setting · PDF questions still work")
                st.caption("The plan label is set by TWELVE_DATA_PLAN; it is not auto-detected from the API key.")
                st.caption("Basic coverage in this app is the default US listed-equity feed; Nordic exchanges require Pro and OTC data needs a separate licence.")
        except RuntimeError:
            st.caption("⚪ Market provider status unavailable")
        st.caption(f"📄 {len(docs)} financial reports indexed")
        st.divider()
        if st.button("New conversation", use_container_width=True):
            st.session_state.chat_messages = []
            st.session_state.market_listing_memory = {}
            st.rerun()
    return selection


def as_decimal(value) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _format_price(value) -> str:
    num = as_decimal(value)
    if num is None:
        return "Unavailable"
    if num >= 1:
        return f"{num:,.2f}"
    return f"{num:,.4f}"


def source_lines(result: dict) -> None:
    seen = set()
    for src in result.get("sources") or []:
        if not isinstance(src, dict):
            continue
        if src.get("provider") == "Twelve Data":
            label = f"📈 Twelve Data · {src.get('symbol', '?')} · {src.get('exchange', '?')} · {src.get('provider_datetime') or 'historical series'}"
        else:
            label = f"📄 {src.get('report') or src.get('document') or 'Report'} · PDF page {src.get('page', '?')}"
        if label not in seen:
            st.caption(label)
            seen.add(label)


def _date_axis_domain(frame: pd.DataFrame) -> list:
    """Exact first/last observations; avoids Altair's automatic future padding."""
    ordered = frame.sort_values("Date").drop_duplicates(subset=["Date"])
    if ordered.empty:
        return []
    first = ordered["Date"].iloc[0]
    last = ordered["Date"].iloc[-1]
    return [first.to_pydatetime(), last.to_pydatetime()]


def _actual_date_ticks(frame: pd.DataFrame, maximum: int = 6) -> list:
    """Choose axis ticks only from dates that actually exist in the price series."""
    ordered = frame.sort_values("Date").drop_duplicates(subset=["Date"])
    if ordered.empty:
        return []
    count = min(maximum, len(ordered))
    if count == 1:
        indexes = [0]
    else:
        indexes = sorted({round(i * (len(ordered) - 1) / (count - 1)) for i in range(count)})
    return [ordered["Date"].iloc[index].to_pydatetime() for index in indexes]


def history_chart(market: dict) -> None:
    points = market.get("points") or []
    rows = []
    for point in points:
        number = as_decimal(point.get("close"))
        if number is not None:
            rows.append({"Date": point["date"], "Close": float(number)})
    if rows:
        frame = pd.DataFrame(rows)
        frame["Date"] = pd.to_datetime(frame["Date"])
        frame = frame.sort_values("Date").drop_duplicates(subset=["Date"])
        domain = _date_axis_domain(frame)
        ticks = _actual_date_ticks(frame, maximum=6)
        chart = alt.Chart(frame).mark_line(point=False).encode(
            x=alt.X("Date:T", scale=alt.Scale(domain=domain, nice=False, clamp=True),
                    axis=alt.Axis(values=ticks, format="%b %d", labelOverlap=True), title=None),
            y=alt.Y("Close:Q", scale=alt.Scale(zero=False), title=f"Close ({market.get('currency') or 'price'})"),
            tooltip=[alt.Tooltip("Date:T", title="Date"), alt.Tooltip("Close:Q", format=",.2f", title="Close")],
        ).properties(height=300)
        st.altair_chart(chart, use_container_width=True)
        st.caption(f"Observed dates: {frame['Date'].iloc[0].date()} to {frame['Date'].iloc[-1].date()}. Provider closing prices; dividends excluded and data may be delayed.")


def compare_chart(market: dict) -> None:
    rows = []
    for item in market.get("chart_points") or []:
        one, two = as_decimal(item.get("first_pct")), as_decimal(item.get("second_pct"))
        if one is not None and two is not None:
            rows.append({"Date": item["date"], "Stock 1 (%)": float(one), "Stock 2 (%)": float(two)})
    if rows:
        frame = pd.DataFrame(rows)
        frame["Date"] = pd.to_datetime(frame["Date"])
        frame = frame.sort_values("Date").drop_duplicates(subset=["Date"])
        domain = _date_axis_domain(frame)
        ticks = _actual_date_ticks(frame, maximum=7)
        chart = alt.Chart(frame).transform_fold(
            ["Stock 1 (%)", "Stock 2 (%)"], as_=["Instrument", "Return"]
        ).mark_line().encode(
            x=alt.X("Date:T", scale=alt.Scale(domain=domain, nice=False, clamp=True),
                    axis=alt.Axis(values=ticks, format="%b %d", labelOverlap=True), title=None),
            y=alt.Y("Return:Q", scale=alt.Scale(zero=False), title="Price-only return (%)"),
            color=alt.Color("Instrument:N", title="Series"),
            tooltip=[alt.Tooltip("Date:T", title="Date"), alt.Tooltip("Instrument:N"),
                     alt.Tooltip("Return:Q", format="+.2f", title="Return (%)")],
        ).properties(height=320)
        st.altair_chart(chart, use_container_width=True)
        st.caption(f"Shared observations: {len(frame)} · {frame['Date'].iloc[0].date()} to {frame['Date'].iloc[-1].date()}. Price-only percentage returns; dividends excluded.")


def render_result(result: dict, index: int, active: bool, selection: dict) -> None:
    kind = result.get("kind", "document")
    answer = result.get("answer", "No answer returned.")
    if kind == "combined" and result.get("error_code"):
        st.warning(result.get("market_message") or answer)
        doc = result.get("document") or {}
        st.markdown("**Financial-report evidence**")
        st.markdown(doc.get("answer") or "No verified report answer.")
        if not doc.get("supported"):
            st.info("The financial-report portion could not be verified either.")
        st.caption("The market-data portion was unavailable under the configured plan or provider status; the report result above was assessed separately.")
        source_lines(result)
        return
    if kind == "market_unavailable" or result.get("error_code"):
        st.warning(answer)
        source_lines(result)
        if active and st.button("Try again", key=f"retry_{index}"):
            original = st.session_state.chat_messages[index].get("question")
            if original:
                submit(original, selection)
        return
    if kind == "market_clarification":
        st.markdown(answer)
        choices = result.get("candidates") or []
        if choices and active:
            st.caption("Choose one of the verified listings returned by the market-data provider:")
            # These choices are returned by the market provider, not guessed by the UI.
            for j, candidate in enumerate(choices[:8]):
                entitlement = candidate.get("entitlement") or {}
                security_label = entitlement.get("security_label") or candidate.get("security_label")
                label_parts = [candidate.get("name") or candidate.get("symbol"), candidate.get("symbol"),
                               candidate.get("exchange"), candidate.get("currency") or "currency unknown"]
                if security_label:
                    label_parts.append(str(security_label))
                if not entitlement.get("allowed", True):
                    label_parts.append(entitlement.get("entitlement_label") or "Unavailable on configured plan")
                label = " · ".join(str(part) for part in label_parts if part)
                available = bool(entitlement.get("allowed", True))
                if st.button(label, key=f"listing_{index}_{j}", use_container_width=True, disabled=not available):
                    message = st.session_state.chat_messages[index]
                    selected = list(result.get("selected_listings") or [])
                    entity_key = result.get("entity_key") or candidate.get("symbol")
                    chosen = {"symbol": candidate["symbol"], "exchange": candidate["exchange"],
                              "entity_key": entity_key}
                    selected.append(chosen)
                    memory = st.session_state.setdefault("market_listing_memory", {})
                    for alias in (entity_key, candidate.get("symbol"), candidate.get("name")):
                        key = _listing_memory_key(str(alias or ""))
                        if key:
                            memory[key] = chosen
                    message["resolved"] = True
                    submit(message["question"], selection, selected_listings=selected, echo_user=False)
                if not available and entitlement.get("message"):
                    st.caption(entitlement["message"])
        elif choices and not active:
            st.caption("A listing was chosen or the conversation continued.")
        # A combined question can already have a verified PDF answer while the
        # user is choosing an exchange. Keep that report evidence visible here.
        doc = result.get("document") or {}
        if doc:
            st.markdown("**Financial-report evidence**")
            st.markdown(doc.get("answer") or "No verified report answer.")
            if not doc.get("supported"):
                st.info("The report portion wasn't verified. The market listing is still awaiting selection.")
            source_lines(result)
        return
    if kind in ("market_quote", "combined"):
        market = result.get("market") or {}
        if market:
            quote_label = f"{market.get('company_name') or market.get('symbol')} · {market.get('symbol')} · {market.get('exchange')}"
            if market.get("security_label"):
                quote_label += f" · {market['security_label']}"
            quote_label += " · last available close"
            st.metric(label=quote_label, value=f"{market.get('currency', '')} {_format_price(market.get('close'))}")
            if market.get("security_note"):
                st.info(market["security_note"])
            change = as_decimal(market.get("percent_change"))
            if change is not None:
                st.caption(f"Provider-reported change: {change:+.2f}%")
            st.caption(f"Provider time: {market.get('provider_datetime') or '?'} · Retrieved: {market.get('fetched_at_utc') or '?'}")
        if kind == "combined":
            st.markdown("**Financial-report evidence**")
            doc = result.get("document") or {}
            st.markdown(doc.get("answer") or "No verified report answer.")
            if not doc.get("supported"):
                st.info("The report portion wasn't verified. The stock quotation does not establish any report metric.")
        else:
            st.caption("Quote is provider-reported and not guaranteed real-time.")
    elif kind == "market_history":
        st.markdown(answer)
        history_chart(result.get("market") or {})
    elif kind == "market_compare":
        st.markdown(answer)
        market = result.get("market") or {}
        cols = st.columns(2)
        for col, row in zip(cols, market.get("results") or []):
            with col:
                val = as_decimal(row.get("return_percent"))
                st.metric(f"{row.get('symbol')} · {row.get('exchange')}", f"{val:+.2f}%" if val is not None else "Unavailable")
        compare_chart(market)
    else:
        st.markdown(answer)
        if result.get("needs_clarification") and kind == "overview":
            st.info("Please mention an indexed bank or select one report in the sidebar. For a specific edition, include its quarter (for example, Q3).")
        elif result.get("needs_clarification"):
            st.info("Please make the metric or financial report more specific.")
        elif result.get("supported") is False:
            st.caption("No sufficient verified report evidence found for that question.")
    source_lines(result)


def render_fun_reaction(question: str, result: dict) -> None:
    """Render the optional reaction as presentation only; missing art is harmless."""
    try:
        from app.fun_mode import choose_reaction, image_path
        reaction = choose_reaction(
            question,
            str(result.get("answer") or ""),
            bool(result.get("supported")),
        )
        st.markdown(f"**{reaction.headline}**")
        st.caption(reaction.caption)
        # Some project snapshots contain the classifier but not its optional
        # illustration bundle. Keep Fun Mode usable with its emoji/text fallback.
        artwork = image_path(reaction)
        if artwork.is_file():
            st.image(str(artwork), width=360)
    except Exception:
        # A decorative feature must never break a financial answer.
        st.caption("✨ Fun mode reaction unavailable for this message.")


def _context_for_followup(question: str) -> tuple[str | None, str | None]:
    """Return the previous question and clarification prompt for a short slot reply."""
    if not (PERIOD_ONLY_FOLLOWUP.fullmatch(question or "")
            or SHORT_SLOT_REPLY.fullmatch(question or "")):
        return None, None
    clarification_prompt = None
    for item in reversed(st.session_state.chat_messages):
        if item.get("role") == "assistant":
            result = item.get("result") or {}
            if not result.get("needs_clarification") and not result.get("context_required"):
                return None, None
            clarification_prompt = str(result.get("answer") or item.get("content") or "")
            carried_context = result.get("clarification_context_question")
            if isinstance(carried_context, str) and carried_context.strip():
                return carried_context.strip(), clarification_prompt
            continue
        if item.get("role") == "user" and clarification_prompt:
            content = item.get("content")
            return (content if isinstance(content, str) else None), clarification_prompt
    return None, None


def _listing_memory_key(value: str) -> str:
    value = re.sub(r"[^a-z0-9 ]+", " ", (value or "").casefold()).strip()
    value = re.sub(r"\s+", " ", value)
    aliases = {"nordea bank abp": "nordea", "nordea bank": "nordea",
               "nda": "nordea", "nbnkf": "nordea", "nrdby": "nordea"}
    if value in aliases:
        return aliases[value]
    parts = value.split()
    while parts and parts[-1] in {"inc", "incorporated", "corp", "corporation", "plc", "abp", "ltd", "limited", "sa", "nv", "se"}:
        parts.pop()
    return " ".join(parts)[:80]


def submit(question: str, selection: dict, *, selected_listings=None, echo_user: bool = True) -> None:
    context_question, context_prompt = _context_for_followup(question)
    # Snapshot only earlier turns before appending the current user message.
    # Sending the current question twice caused contextual follow-ups such as
    # "How does that compare with Q3 2026?" to resolve against themselves.
    history = [
        {"role": item["role"], "content": item["content"]}
        for item in st.session_state.chat_messages[-20:]
        if item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
        and item["content"].strip()
    ]
    history = [{**item, "content": item["content"][-4000:]} for item in history]
    if echo_user:
        st.session_state.chat_messages.append({"role": "user", "content": question})
    with st.spinner("Checking sources..."):
        try:
            result = request("POST", "/chat_v1", json={
                "question": question,
                "context_question": context_question,
                "context_prompt": context_prompt,
                "document_selection": selection,
                "selected_listings": selected_listings or [],
                "remembered_listings": st.session_state.get("market_listing_memory", {}),
                "history": history,
            })
        except RuntimeError as exc:
            result = {"kind": "market_unavailable", "answer": str(exc), "error_code": "api_request_failed",
                      "sources": [], "supported": False}
    st.session_state.chat_messages.append({"role": "assistant", "content": result.get("answer", ""),
                                          "result": result, "question": question, "resolved": False})
    if len(st.session_state.chat_messages) > 120:
        st.session_state.chat_messages = st.session_state.chat_messages[-120:]
    st.rerun()


if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []
if "market_listing_memory" not in st.session_state:
    st.session_state.market_listing_memory = {}

try:
    indexed_reports = get_documents()
    offline_message = ""
except RuntimeError as error:
    indexed_reports = []
    try:
        get_status()  # A temporary /documents failure must not disable market questions.
        offline_message = ""
    except RuntimeError:
        offline_message = str(error)

selection = selection_sidebar(indexed_reports)
st.title("💬 Financial Intelligence")
st.caption("Ask about financial reports, stock prices, or both. Sources are checked before answers appear.")
fun_mode_active = st.checkbox(
    "🎭 Fun mode",
    key="fun_mode_enabled",
    help="Show an optional, presentation-only reaction under assistant answers. It never changes financial results.",
)
if fun_mode_active:
    st.caption("Fun mode is on. Reactions appear beneath assistant answers; financial results are unchanged.")
st.info(
    "Educational portfolio demonstration by Serhat Turan. Twelve Data Basic provides the default US listed-equity feed; "
    "instrument and exchange coverage varies. Nordic primary exchanges require Pro access and OTC securities require a separate licence. "
    "Quotes may be delayed; not investment advice.",
    icon="ℹ️",
)
with st.expander("Capabilities, data coverage & limitations", expanded=False):
    st.markdown("""
    **What this assistant can do**
    - Answer questions using the financial reports indexed in this installation, with document and PDF-page citations when supporting evidence is available.
    - Retrieve supported stock quotes and historical price data through Twelve Data Basic.
    - Compare price-only returns over common trading dates, and combine market data with report evidence while keeping the sources separate.

    **Important limitations**
    - The configured Twelve Data Basic feed is the default US listed-equity feed. Nasdaq Helsinki, Stockholm and Copenhagen are listed as Pro exchanges by Twelve Data. OTC securities are excluded from default plans and require a separate licence. The assistant must not silently substitute an OTC listing for a primary exchange listing.
    - Market quotations may be delayed and are not guaranteed real-time. Check the provider timestamp and exchange before relying on a quote.
    - Financial-report answers are limited to the reports indexed in this installation. Citations identify the source document and PDF page.
    - This educational project does not execute trades, provide personalized investment recommendations, or guarantee forecasts. It is not investment advice.

    **Project author**

    Designed and developed by **Serhat Turan**, a Mathematics graduate building a career in data science and AI engineering.
    """)

if not st.session_state.chat_messages:
    st.markdown("#### Recommended questions")
    samples = [
        "Who made you?",
        "What can you do?",
        "Tell me about Nordea",
        "Tell me about Nordea Q3",
        "What was Nordea's CET1 ratio in Q3 2026?",
        "What's Apple's latest stock price?",
        "Show AAPL's stock performance over the past 3 months",
        "Compare AAPL and MSFT stock performance over 6 months",
        "What's Nordea's stock price and its CET1 ratio in Q3 2026?",
    ]
    for i, example in enumerate(samples):
        if st.button(example, key=f"example_{i}", use_container_width=True, disabled=bool(offline_message)):
            submit(example, selection)
else:
    for index, message in enumerate(st.session_state.chat_messages):
        with st.chat_message(message["role"]):
            if message["role"] == "user":
                st.markdown(message["content"])
            else:
                render_result(message["result"], index,
                              active=index == len(st.session_state.chat_messages) - 1 and not message.get("resolved"),
                              selection=selection)
                if fun_mode_active:
                    render_fun_reaction(message.get("question", ""), message["result"])

if offline_message:
    st.warning("Can't connect to the API. Restart it with `python -m uvicorn app.api_day13_6:app --host 127.0.0.1 --port 8000`.")
question = st.chat_input("Ask about reports, stocks, or markets...", max_chars=4000, disabled=bool(offline_message))
if question:
    submit(question, selection)

st.caption("Educational demonstration · Market data may be delayed · Not investment advice")
