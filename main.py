import uuid
from langgraph.graph import START, StateGraph
from dotenv import load_dotenv
from typing import List, TypedDict, Annotated
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langgraph.graph.message import add_messages
import psycopg
from langgraph.store.postgres import PostgresStore
from pydantic import BaseModel, Field
from langgraph.store.base import BaseStore
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command 

# Imports from other files
from prompts import MEMORY_PROMPT, SYSTEM_PROMPT_TEMPLATE
from CONFIG import GROQ_MODEL, OPENAI_MODEL, TEMPERATURE, POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER
from tool import run_headhunter_agent, read_good_jobs_report

#-------------------------------------- Load and init LLMs ------------------------------------------
load_dotenv()
groq_llm = ChatGroq(model=GROQ_MODEL, temperature=TEMPERATURE)
# openai_llm = ChatOpenAI(model=OPENAI_MODEL, temperature=TEMPERATURE)

tools = [run_headhunter_agent, read_good_jobs_report]
# openai_tooling = openai_llm.bind_tools(tools)
groq_tooling = groq_llm.bind_tools(tools)

#--------------------------------------- Build classes -------------------------------------------
class state_class(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

class pydantic_1(BaseModel):
    text: str = Field(description="atomic user memory")
    is_new: bool = Field(description="True if new memory, False if already exists")

class pydantic_2(BaseModel):
    should_add: bool = Field(description="True if able to add, False if not")
    memories: List[pydantic_1] = Field(default_factory=list)

pydantic_llm = groq_llm.with_structured_output(pydantic_2)
    
#----------------------------------------- Define Nodes --------------------------------------------
#------------ Remember Nodes ------------
def remember_node(state: state_class, config: RunnableConfig, store: BaseStore):
    """Extract and store user's personal memories for long-term-storage, skip the generals"""
    try:
        user_id = config['configurable']['user_id']
        namespace = ('user', user_id, 'details')
        items = store.search(namespace)
        existing_memories = "\n".join(i.value.get('data', '') for i in items) if items else "(empty)"

        last_message = state['messages'][-1].content
        
        decision = pydantic_llm.invoke([
            SystemMessage(content=MEMORY_PROMPT.format(user_details_content=existing_memories)),
            HumanMessage(content=last_message)
        ])
        
        if decision.should_add:
            for mem in decision.memories:
                if mem.is_new and mem.text.strip():
                    store.put(namespace, str(uuid.uuid4()), {'data': mem.text.strip()})
    except Exception as e:
        print(f"⚠️ Memory error: {e}")
    return {}

#-------------- Chat Nodes --------------
def chat_node(state: state_class, config: RunnableConfig, store: BaseStore):
    """Generate response using memories, and show personalization with the respect of user"""
    try:
        user_id = config['configurable']['user_id']
        namespace = ('user', user_id, 'details')
        items = store.search(namespace)
        user_details = "\n".join(it.value.get("data", "") for it in items) if items else "(empty)"
        
        system_msg = SystemMessage(
            content=SYSTEM_PROMPT_TEMPLATE.format(user_details_content=user_details)
        )
        response = groq_tooling.invoke([system_msg] + state["messages"])
        return {"messages": [response]}
    
    except Exception as e:
        print(f"⚠️ Chat error: {e}")
        return {"messages": [HumanMessage(content="Sorry, I encountered an error. Please try again.")]}
    
tool_node = ToolNode(tools)

def tools_with_logging(state: state_class):
    print(f"⚙️ Executing tools...")
    result = tool_node.invoke(state)
    print(f"✅ Tool execution completed\n")
    return result

# ==============================================================================
# 🔑 GLOBAL GRAPH DEFINITION (Renamed to 'builder' for Import)
# ==============================================================================
builder = StateGraph(state_class)
builder.add_node('chat_node', chat_node)
builder.add_node('remember_node', remember_node)
builder.add_node("tools", tools_with_logging)

builder.add_edge(START, 'remember_node')
builder.add_edge('remember_node', 'chat_node')
builder.add_conditional_edges("chat_node", tools_condition)
builder.add_edge("tools", "chat_node")

#----------------------------------------- Main Function --------------------------------------------
def main():
    # Database connection
    DB_URI = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@localhost:5442/{POSTGRES_DB}?sslmode=disable"
    
    # We use 'builder' which is now defined globally above
    with PostgresStore.from_conn_string(DB_URI) as store, \
        PostgresSaver.from_conn_string(DB_URI) as checkpointer:

        store.setup()
        checkpointer.setup()
        
        # Compile the global builder
        bot = builder.compile(store=store, checkpointer=checkpointer)

        user_name = 'CLI_User_v1'
        thread_id = 'CLI_Thread_v1'
        config = {'configurable': {'user_id': user_name, 'thread_id': thread_id}}
        
        # print("🤖 HEADHUNTER READY! (Type 'exit' to quit)\n")
        
        while True:
            try:
                # 1. Check Interrupts
                snapshot = bot.get_state(config)
                if snapshot.next and len(snapshot.tasks) > 0 and snapshot.tasks[0].interrupts:
                    interrupt_val = snapshot.tasks[0].interrupts[0].value
                    print(f"\n⚠️  ACTION REQUIRED: {interrupt_val}")
                    user_decision = input("👉 Your Answer (yes/no): ")
                    response = bot.invoke(Command(resume=user_decision), config)
                else:
                    user_input = input("\nYou: ")
                    if user_input.lower().strip() in ['exit', 'bye', 'quit']:
                        break
                    if not user_input.strip():
                        continue
                    response = bot.invoke({"messages": [{"role": "user", "content": user_input}]}, config)

                # 2. Print Response
                if response and "messages" in response and len(response["messages"]) > 0:
                    print(f"🤖: {response['messages'][-1].content}\n")
        
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"⚠️ Error: {e}\n")

def datastore_loaded():
    """Load all rows from the datastore"""
    DB_URI = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@localhost:5442/{POSTGRES_DB}?sslmode=disable"
    with psycopg.connect(DB_URI) as conn:
        cur = conn.cursor()  # Fixed: use cursor() not connect()
        cur.execute("SELECT * FROM store;")
        rows = cur.fetchall()
        return rows

def checkpoints_loaded():
    """Load all checkpoints from the database"""
    DB_URI = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@localhost:5442/{POSTGRES_DB}?sslmode=disable"
    with psycopg.connect(DB_URI) as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT thread_id, COUNT(*) as checkpoint_count
            FROM checkpoints 
            GROUP BY thread_id 
            ORDER BY thread_id;
        """)
        threads = cur.fetchall()
        return threads
    
if __name__ == '__main__':
    main()