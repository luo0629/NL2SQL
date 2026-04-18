from app.agent.state import AgentState


def retrieve_schema(state: AgentState) -> AgentState:
    state["schema_context"] = ["mock-schema"]
    return state


def generate_sql(state: AgentState) -> AgentState:
    question = state.get("question", "")
    state["sql"] = f"-- generated for: {question}"
    return state
