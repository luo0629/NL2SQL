from app.agent.nodes import generate_sql, retrieve_schema
from app.agent.state import AgentState


def run_agent(question: str) -> AgentState:
    state: AgentState = {"question": question}
    state = retrieve_schema(state)
    state = generate_sql(state)
    return state
