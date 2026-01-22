import uuid
from langgraph.graph import START, END, StateGraph
from dotenv import load_dotenv
from typing import List, TypedDict, Annotated
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from CONFIG import GROQ_MODEL, OPENAI_MODEL, TEMPERATURE, POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER
from langgraph.graph.message import add_messages
from langgraph.store.postgres import PostgresStore
from pydantic import BaseModel, Field
from langgraph.store.base import BaseStore
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode, tools_condition
from prompts import MEMORY_PROMPT, SYSTEM_PROMPT_TEMPLATE
from langgraph.checkpoint.postgres import PostgresSaver

# --- NEW IMPORTS FOR HUMAN-IN-THE-LOOP ---
from langgraph.types import Command, Interrupt 
from tool import run_headhunter_agent, read_good_jobs_report

#-------------------------------------- Load and init LLMs ------------------------------------------
load_dotenv()
groq_llm = ChatGroq(model=GROQ_MODEL, temperature=TEMPERATURE)
openai_llm = ChatOpenAI(model=OPENAI_MODEL, temperature=TEMPERATURE)

tools = [run_headhunter_agent, read_good_jobs_report]
# groq_tooling = groq_llm.bind_tools(tools)
openai_tooling = openai_llm.bind_tools(tools)

#--------------------------------------- Build classes -------------------------------------------
class state_class(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

class pydantic_1(BaseModel):
    text: str = Field(description="atomic user memory")
    is_new: bool = Field(description="True if new memory, False if already exists")

class pydantic_2(BaseModel):
    should_add: bool = Field(description="True if able to add, False if not")
    memories: List[pydantic_1] = Field(default_factory=list)

pydantic_llm = openai_llm.with_structured_output(pydantic_2)
    
#----------------------------------------- Define Nodes --------------------------------------------
#------------ Remember Nodes -------------
def remember_node(state: state_class, config: RunnableConfig, store: BaseStore):
    """Extract and store user memories"""
    try:
        user_id = config['configurable']['user_id']
        namespace = ('user', user_id, 'details')
        
        # Retrieve existing memories
        items = store.search(namespace)
        existing_memories = "\n".join(i.value.get('data', '') for i in items) if items else "(empty)"
        
        last_message = state['messages'][-1].content
        
        # Ask LLM to extract memories
        decision: pydantic_2 = pydantic_llm.invoke([
            SystemMessage(content=MEMORY_PROMPT.format(user_details_content=existing_memories)),
            HumanMessage(content=last_message)
        ])
        
        # Store new memories
        if decision.should_add:
            for mem in decision.memories:
                if mem.is_new and mem.text.strip():
                    store.put(namespace, str(uuid.uuid4()), {'data': mem.text.strip()})
                    
    except Exception as e:
        print(f"⚠️ Memory error: {e}")
    
    return {}

#------------- Chat Nodes --------------
def chat_node(state: state_class, config: RunnableConfig, store: BaseStore):
    """Generate response using memories"""
    try:
        user_id = config['configurable']['user_id']
        namespace = ('user', user_id, 'details')
        
        # Retrieve memories
        items = store.search(namespace)
        user_details = "\n".join(it.value.get("data", "") for it in items) if items else "(empty)"
        
        # Build system message with memories
        system_msg = SystemMessage(
            content=SYSTEM_PROMPT_TEMPLATE.format(user_details_content=user_details)
        )
        
        # Generate response
        response = openai_tooling.invoke([system_msg] + state["messages"])
        return {"messages": [response]}
        
    except Exception as e:
        print(f"⚠️ Chat error: {e}")
        return {"messages": [HumanMessage(content="Sorry, I encountered an error. Please try again.")]}
    
#------------ Tool Nodes -------------
tool_node = ToolNode(tools)

#----------------------------------------- Main Function --------------------------------------------
def main():
    # Build graph
    graph = StateGraph(state_class)
    graph.add_node('chat_node', chat_node)
    graph.add_node('remember_node', remember_node)
    
    graph.add_edge(START, 'remember_node')
    graph.add_edge('remember_node', 'chat_node')
    
    if tool_node:
        def tools_with_logging(state: state_class):
            print(f"⚙️ Executing tools...")
            result =  tool_node.invoke(state)
            print(f"✅ Tool execution completed\n")
            return result
            
        graph.add_node("tools", tools_with_logging)

        graph.add_conditional_edges("chat_node", tools_condition)
        graph.add_edge("tools", "chat_node")
    else:
        graph.add_edge("chat_node", END)

    # Database connection
    DB_URI = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@localhost:5442/{POSTGRES_DB}?sslmode=disable"
    with PostgresStore.from_conn_string(DB_URI) as store, \
        PostgresSaver.from_conn_string(DB_URI) as checkpointer:

        store.setup()
        checkpointer.setup()
        bot = graph.compile(
            store=store,
            checkpointer=checkpointer
        )

        user_name = 'update_user_01'
        thread_id = 'update_thread_01'
        config = {'configurable': {'user_id': user_name, 'thread_id': thread_id}}
        
        print("🤖 HEADHUNTER READY! (Type 'exit' to quit)\n")
        
        # ------------------------------------------------------------------
        # NEW MAIN LOOP: HANDLES HUMAN-IN-THE-LOOP (INTERRUPTS)
        # ------------------------------------------------------------------
        while True:
            try:
                # 1. Check if the graph is paused (Interrupted)
                snapshot = bot.get_state(config)
                
                if snapshot.next and len(snapshot.tasks) > 0 and snapshot.tasks[0].interrupts:
                    # --- RESUME MODE ---
                    interrupt_val = snapshot.tasks[0].interrupts[0].value
                    print(f"\n⚠️  ACTION REQUIRED: {interrupt_val}")
                    
                    user_decision = input("👉 Your Answer (yes/no): ")
                    
                    # Resume execution with the user's answer
                    response = bot.invoke(
                        Command(resume=user_decision), 
                        config
                    )
                
                else:
                    # --- NORMAL CHAT MODE ---
                    user_input = input("\nYou: ")
                    
                    if user_input.lower().strip() in ['exit', 'bye', 'quit']:
                        print('👋 Thanks for chatting!')
                        break
                    
                    if not user_input.strip():
                        continue
                    
                    response = bot.invoke(
                        {"messages": [{"role": "user", "content": user_input}]}, 
                        config
                    )

                # 2. Print Bot Response
                if response and "messages" in response and len(response["messages"]) > 0:
                    print(f"🤖: {response['messages'][-1].content}\n")
                    
                # 3. Print Memory (Optional Debug)
                namespace = ('user', user_name, 'details')
                previous = store.search(namespace)
                # Uncomment below if you want to see memory every turn
                for it in previous:
                   print(f"STORED DATA:- {it.value['data']}")
        
            except KeyboardInterrupt:
                print("\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"⚠️ Error processing message: {e}\n")

if __name__ == '__main__':
    main()