
from app.observability import trace
from typing import TypedDict, Any
from langgraph.graph import StateGraph, START, END
from app.aws import embed, answer
from app.vector import search
from app.config import settings
from app.guardrails import check_input, check_output
from app.llm import get_default_gateway


class State(TypedDict, total=False):
    question: str
    principal: Any
    passages: list
    answer: str
    sources: list
    status: str
    history: list
    gateway: Any
    routing: dict


@trace("guard")
def guard(s):
    return {
        'question': check_input(s['question'])
    }


@trace("retrieve")
def retrieve(s):
    p = s['principal']

    # Include the most recent user question for short follow-ups.
    prior_questions = []

    for message in s.get('history', []):
        if message['role'] == 'user':
            prior_questions.append(message['content'])

    if prior_questions:
        query = (prior_questions[-1] + '\nFollow-up: ' + s['question'])
    else:
        query = s['question']

    hits = search(
        p.tenant,
        query,
        embed(query),
        p.groups,
        p.subject
    )

    # Keep only relevant search results.
    relevant_hits = []

    for hit in hits:
        relevant_hits.append(hit)

  
    sources = []

    for i, hit in enumerate(relevant_hits):
        source = {
            'index': i + 1,
            'document_id': hit['metadata'].get('document_id', ''),
            'title': hit['metadata'].get('title', ''),
            'page': hit['metadata'].get('page', 1),
            'excerpt': hit['metadata'].get('text', '')[:320]
        }

        sources.append(source)

    return {
        'passages': relevant_hits,
        'sources': sources
    }


def route(s):
    if s['passages']:
        return 'generate'

    return 'no_evidence'


@trace("no_evidence")
def no_evidence(s):
    return {
        'answer': (
            'I could not find supporting evidence in documents '
            'you are authorized to access.'
        ),
        'status': 'no_evidence'
    }


@trace("generate")
def generate(s):
    evidence_parts = []

    for i, hit in enumerate(s['passages']):
        title = hit['metadata'].get('title', 'Document')
        page = hit['metadata'].get('page', 1)
        text = hit['metadata'].get('text', '')

        evidence_part = (
            f'[{i + 1}] {title}, page {page}: {text}'
        )

        evidence_parts.append(evidence_part)

    evidence = '\n\n'.join(evidence_parts)

    gw = s.get('gateway') or get_default_gateway()

    generated_answer, decision = gw.generate_answer(
        question=s['question'],
        evidence=evidence,
        history=s.get('history', [])
    )

    return {
        'answer': generated_answer,
        'status': 'answered',
        'routing': decision.model_dump()
    }


@trace("validate")
def validate(s):
    return {
        'answer': check_output(s['answer'])
    }


def make_graph():
    g = StateGraph(State)

    nodes = [
        ('guard', guard),
        ('retrieve', retrieve),
        ('no_evidence', no_evidence),
        ('generate', generate),
        ('validate', validate)
    ]

    for name, fn in nodes:
        g.add_node(name, fn)

    g.add_edge(START, 'guard')
    g.add_edge('guard', 'retrieve')

    g.add_conditional_edges(
        'retrieve',
        route,
        {
            'generate': 'generate',
            'no_evidence': 'no_evidence'
        }
    )

    g.add_edge('generate', 'validate')
    g.add_edge('validate', END)
    g.add_edge('no_evidence', END)

    return g.compile()


graph = make_graph()


@trace("chat")
def chat(p, question, history=None, gateway=None):
    r = graph.invoke({
        'principal': p,
        'question': question,
        'history': history or [],
        'gateway': gateway or get_default_gateway()
    })

    return {
        'answer': r['answer'],
        'sources': r.get('sources', []),
        'status': r.get('status', 'answered'),
        'routing': r.get('routing', {})
    }
