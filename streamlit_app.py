import streamlit as st
import uuid
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from langgraph.types import Command
from dotenv import load_dotenv
from CONFIG import POSTGRES_DB_URL
DB_URI = POSTGRES_DB_URL

# 1. IMPORT YOUR AGENT BRAIN
# This works now because 'builder' is global in main.py
from main import builder  
from CONFIG import POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER

load_dotenv()

# ==============================================================================
# 2. CONFIGURATION
# ==============================================================================

st.set_page_config(page_title="Agent HeadHunter", page_icon="🤖", layout="wide")
st.title("🤖 Agent HeadHunter")

# Database Connection (Must match main.py)

# Initialize Memory (Session State)
if "messages" not in st.session_state:
    st.session_state.messages = []
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "awaiting_approval" not in st.session_state:
    st.session_state.awaiting_approval = False
if "approval_data" not in st.session_state:
    st.session_state.approval_data = None

# ==============================================================================
# 3. CORE AGENT RUNNER
# ==============================================================================

def run_agent_graph(user_input=None, resume_value=None):
    """
    Connects to the DB, compiles the graph, and runs one turn of the agent.
    """
    # Config for this specific user/thread
    config = {
        "configurable": {
            "user_id": "STREAMLIT_USER", 
            "thread_id": st.session_state.thread_id
        }
    }
    
    # Open DB Connection safely
    with PostgresStore.from_conn_string(DB_URI) as store, PostgresSaver.from_conn_string(DB_URI) as checkpointer:
        store.setup()
        checkpointer.setup()
        
        # Compile the graph using the builder imported from main.py
        graph = builder.compile(store=store, checkpointer=checkpointer)
        
        try:
            # Determine Input Type (New Message vs Resume)
            if resume_value:
                # We are resuming a paused state (e.g. after Payment Approval)
                command = Command(resume=resume_value)
                events = graph.stream(command, config=config)
            else:
                # We are starting a new conversation turn
                command = {"messages": [HumanMessage(content=user_input)]}
                events = graph.stream(command, config=config)

            # Process the stream (we just wait for it to finish)
            for event in events:
                pass 
                
            # Get the final state to see if it paused or finished
            snapshot = graph.get_state(config)
            return snapshot
            
        except Exception as e:
            st.error(f"❌ Execution Error: {e}")
            return None

# ==============================================================================
# 4. USER INTERFACE
# ==============================================================================

# A. Display Chat History
for msg in st.session_state.messages:
    role = msg["role"]
    # Convert 'assistant' to 'ai' icon if you want, or keep defaults
    with st.chat_message(role):
        st.markdown(msg["content"])

# B. Handle Interrupts (The "Payment" Buttons)
if st.session_state.awaiting_approval:
    cost_info = st.session_state.approval_data
    
    with st.chat_message("assistant"):
        st.warning(f"⚠️ **APPROVAL REQUIRED:** {cost_info}")
        col1, col2 = st.columns([1, 4])
        
        with col1:
            if st.button("✅ Approve"):
                st.session_state.awaiting_approval = False
                with st.spinner("🚀 Agent is working..."):
                    snapshot = run_agent_graph(resume_value="yes")
                    if snapshot and snapshot.values['messages']:
                        response = snapshot.values['messages'][-1].content
                        st.session_state.messages.append({"role": "assistant", "content": response})
                        st.rerun()

        with col2:
            if st.button("❌ Deny"):
                st.session_state.awaiting_approval = False
                snapshot = run_agent_graph(resume_value="no")
                if snapshot and snapshot.values['messages']:
                    response = snapshot.values['messages'][-1].content
                    st.session_state.messages.append({"role": "assistant", "content": response})
                    st.rerun()

# C. Chat Input (Only show if not waiting for approval)
elif prompt := st.chat_input("Ex: Find AI Engineer jobs in Dubai"):
    
    # 1. Add User Message to History
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 2. Run the Agent
    with st.spinner("🤖 Thinking..."):
        snapshot = run_agent_graph(user_input=prompt)

    # 3. Handle the Result
    if snapshot:
        # Check if the agent paused for approval
        if snapshot.next and snapshot.tasks[0].interrupts:
            # Extract the interrupt value (the question/cost)
            interrupt_value = snapshot.tasks[0].interrupts[0].value
            st.session_state.awaiting_approval = True
            st.session_state.approval_data = interrupt_value
            st.rerun() # Refresh to show buttons
            
        # Otherwise, it finished normally
        elif snapshot.values['messages']:
            last_msg = snapshot.values['messages'][-1].content
            st.session_state.messages.append({"role": "assistant", "content": last_msg})
            st.rerun()