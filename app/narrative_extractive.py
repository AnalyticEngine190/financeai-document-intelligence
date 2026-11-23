"""Strict extractive fallback for directly stated financial disclosures.

This is NOT a general summarizer or an alternative source of financial facts.
When the language model abstains, a *verbatim* one/two-sentence explanation
may be returned only if the selected PDF itself contains a strongly matched
subject AND the requested causal, event, or comparative relationship.

No issuer names, benchmark answers, metric values, or PDF page numbers live
here. Ambiguous/weak evidence causes abstention. It never joins sources, uses
external facts, computes values, or produces a numerical table answer.
"""
from __future__ import annotations
from dataclasses import dataclass
import re

# Generic question words, reporting periods and instruction verbs are not
# identifying nouns. They must not make unrelated passages appear relevant.
_NOISE=frozenset('''a an and are at be by can did do does during for from how in into
is it its of on or our per report reports reported according say said what which
who why with was were them their the this those to two three four five quarter
first second third fourth period year q1 q2 q3 q4
much many did what happened happen about the bank banking
relative compared asked that said its primarily higher lower rise rises rose
increase increased decrease decreased factor factors driver drivers reasons
reason for according amount quarter end'''.split())
# No issuer may be hard-coded in a source-independent extractor. Discard any
# unknown issuer terms via the selected document filename (see _topic_terms).


_CAUSE=re.compile(r'\b(?:because|due to|driven|explained|impacted|attributed|contributed|resulted|reflecting|supported|benefit(?:ted)?|from both|by both|primarily|mainly)\b',re.I)
_RELATION=re.compile(r'\b(?:above|below|exceed(?:ed|ing)?|higher than|lower than|surplus|shortfall|percentage points|regulatory requirement)\b',re.I)
_EVENT=re.compile(r'\b(?:implemented|introduced|approved|changed|adopted|launched|transferred|moved|completed|finalised|deployed|became|was put into|came into force)\b',re.I)
_SENTENCE_BOUNDARY=re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9“])')


def _canonical(s: str) -> str:
    s=re.sub(r'\bCET\s*[- ]?1\b','common equity tier 1',s,flags=re.I)
    s=re.sub(r'\bC\s*/\s*I\b','cost income',s,flags=re.I)
    s=re.sub(r'\bpd\s+model\b','probability default model',s,flags=re.I)
    return s.casefold().replace('\u2011',' ').replace('\u2010',' ').replace('-',' ')


def _stem(w: str) -> str:
    if len(w)>5 and w.endswith('ies'):return w[:-3]+'y'
    if len(w)>5 and w.endswith('s') and not w.endswith(('ss','us')):return w[:-1]
    return w


def _words(s: str) -> list[str]:
    return [_stem(t) for t in re.findall(r'[a-z0-9]+',_canonical(s))]


def _topic_terms(question: str, filename: str) -> set[str]:
    filename_words=set(_words(filename))
    return {w for w in _words(question) if len(w)>=3 and w not in _NOISE
            and w not in filename_words and not w.isnumeric()}


def _intent(question: str) -> str | None:
    s=question.casefold()
    if re.search(r'\b(?:factors?|drivers?|why|reasons?|led to|contributed|credit for|caused)\b',s):
        return 'cause'
    if re.search(r'\b(?:relative to|above|below|compared (?:to|with)|requirement|exceeded|surplus)\b',s):
        return 'relation'
    if re.search(r'\b(?:what happened|what changed|implemented|introduced|approved|presentation change)\b',s):
        return 'event'
    return None


def _relation_found(kind: str, span: str) -> bool:
    return bool({'cause':_CAUSE,'relation':_RELATION,'event':_EVENT}[kind].search(span))


def _sentences(text: str) -> list[str]:
    # Each component is a verbatim substring of the same source page.
    # Avoid accepting partial fragments from a 1,750-character excerpt edge.
    return [s.strip() for s in _SENTENCE_BOUNDARY.split(text) if s.strip()]


@dataclass(frozen=True)
class Candidate:
    text: str
    page: dict
    coverage: float
    matches: int
    strength: int
    score: float


def _candidates(question: str, pages: list[dict]) -> list[Candidate]:
    kind=_intent(question)
    if kind is None:
        return []
    matches=[]
    for p in pages:
        text=str(p.get('text',''))
        if not text.strip() or not p.get('document') or not isinstance(p.get('page'),int):
            continue
        terms=_topic_terms(question,p['document'])
        if len(terms)<2:
            continue
        # Preserve unusual named entities from the user's question, not just
        # a high percentage of common accounting tokens. An unknown named
        # corporation must not receive the selected bank's disclosure.
        names={_stem(w.casefold()) for w in re.findall(r'\b[A-Z][a-zA-Z]{3,}\b',question)
               if _stem(w.casefold()) in terms}
        sentences=_sentences(text)
        # Directly quotable one or two consecutive sentences. The two-sentence
        # option covers ratios followed by their regulatory headroom; it
        # never combines different documents/pages or nonadjacent paragraphs.
        for i in range(len(sentences)):
            for width in (1,2):
                if i+width>len(sentences):continue
                span=' '.join(sentences[i:i+width])
                # A PDF section title may precede a sentence without any
                # terminal punctuation. Drop only an isolated title separated
                # by a true paragraph break, and only if the remaining span
                # itself still contains the entire requested relationship.
                if '\n\n' in span:
                    tail=span.rsplit('\n\n',1)[-1].strip()
                    if _relation_found(kind,tail) and len(set(_words(tail)) & terms)>=2:
                        span=tail
                if not _relation_found(kind,span):continue
                # These are near-verbatim from indexed PDF. Never synthesize
                # an answer from a loosely related description or a heading.
                if not (25<=len(span)<=800):continue
                covered=terms & set(_words(span))
                if names and not names.issubset(covered):
                    continue
                cov=len(covered)/len(terms)
                if len(covered)<2 or cov<0.80:
                    continue
                # Preserve granularity: subject *and* predicate in same sentence
                # or a directly adjacent sentence.  Do not accept because a
                # document-level header mentions a company or period.
                if kind=='event' and 'model' in terms and 'model' not in set(_words(span)):
                    continue
                # Longer two-sentence spans are useful only when they improve
                # subject/predicate relevance; otherwise favor shorter quotes.
                strength=len(covered)
                score=cov*10+strength*0.5+(0.2 if width==1 else 0)-len(span)*0.00015
                # A historical *reported outcome* should outrank a bank's
                # aspiration or future capital policy when a user asks what
                # occurred in a reporting quarter. Do not convert a target
                # or "should exceed" into an achieved result.
                if kind in ('relation','event') and re.search(
                        r'\b(?:should|goal|target|expected|would|could|will|plans to|intended)\b',span,re.I):
                    score-=3.5
                matches.append(Candidate(span,p,cov,len(covered),strength,score))
    return sorted(matches,key=lambda c:(-c.score, c.page['page'],len(c.text)))


def _explicit_quarter(question: str) -> str | None:
    """Only parse a quarter when both quarter and year are explicit."""
    q=question.casefold()
    m=re.search(r'\bq([1-4])\s*(20\d{2})\b',q)
    if m:return f'Q{m[1]}{m[2]}'
    m=re.search(r'\b(first|second|third|fourth) quarter (?:of )?(20\d{2})\b',q)
    if m:return f'Q{1+('first','second','third','fourth').index(m[1])}{m[2]}'
    return None


def _source_edition(pages: list[dict]) -> str | None:
    # The first financial page normally states the edition explicitly. We do
    # not infer it from values or from a bank name. Ignore later historical
    # quarter references in the body of the document.
    for page in sorted(pages,key=lambda p:p.get('page',999999))[:4]:
        top=str(page.get('text',''))[:750]
        m=re.search(r'\bQ([1-4])\s+(20\d{2})\b',top,re.I)
        if m:return f'Q{m[1]}{m[2]}'
    return None


def direct_extractive_answer(question: str, selected_pages: list[dict]) -> dict | None:
    """Return citation-backed *extractive quote*, or None on insufficient evidence.

    All `selected_pages` MUST already have been restricted to a single selected
    document_id by the caller. This routine does not read files or retrieve data.
    It cannot make an otherwise unsupported numerical table cell 'verified'.
    """
    # Reject accidental multi-document input even if the caller later changes.
    names={p.get('document') for p in selected_pages if p.get('text','').strip()}
    if len(names)!=1:return None
    # Questions asserting a counterfactual or a negated proposition are not
    # safely answered by copying a nearby, affirmative source statement.
    if re.search(r'\b(?:not|never|without|unless|hypothetical|forecast|predict|would|could)\b',question,re.I):
        return None
    asked=_explicit_quarter(question)
    edition=_source_edition(selected_pages)
    if asked and edition and asked!=edition:
        # A retrospective question about a *different* quarter needs an
        # explicit period-aligned quote, not the current quarter's narrative.
        return None
    candidates=_candidates(question,selected_pages)
    if not candidates:return None
    top=candidates[0]
    # Guarantee that the source words are present together in this indexed
    # page. Whitespace normalization is permitted; content substitution isn't.
    page_norm=' '.join(str(top.page['text']).split())
    if ' '.join(top.text.split()) not in page_norm:
        return None
    # An extractive answer is the actual statement in the selected PDF,
    # not a model-generated paraphrase. It may be verbose but never invented.
    return {'answer':' '.join(top.text.split()),'supported':True,
            'sources':[{'document':top.page['document'],'page':top.page['page']}],
            'needs_clarification':False}
