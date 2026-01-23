import streamlit as st
import uuid
from langchain_core.messages import HumanMessage, AIMessage
from main import datastore_loaded, checkpoints_loaded
import psycopg
import msgpack
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from langgraph.types import Command
from dotenv import load_dotenv
import time

# 1. IMPORT YOUR AGENT BRAIN
from main import builder, checkpoints_loaded
from CONFIG import POSTGRES_DB, POSTGRES_PASSWORD, POSTGRES_USER

load_dotenv()

# ==============================================================================
# MUST BE FIRST!
# ==============================================================================
st.set_page_config(page_title="Agent HeadHunter", page_icon="🤖", layout="wide")

# ==============================================================================
# 2. CONFIGURATION
# ==============================================================================

DB_URI = f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@localhost:5442/{POSTGRES_DB}?sslmode=disable"

# ==============================================================================
# 3. INITIALIZE SESSION STATE (MUST BE BEFORE SIDEBAR)
# ==============================================================================

if "messages" not in st.session_state:
    st.session_state.messages = []
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())
if "awaiting_approval" not in st.session_state:
    st.session_state.awaiting_approval = False
if "approval_data" not in st.session_state:
    st.session_state.approval_data = None

# ==============================================================================
# 4. HELPER FUNCTIONS
# ==============================================================================

def load_messages_from_checkpoint(thread_id):
    """Load all messages from a specific thread"""
    with psycopg.connect(DB_URI) as conn:
        cur = conn.cursor()
        
        cur.execute("""
            SELECT blob
            FROM checkpoint_blobs
            WHERE thread_id = %s 
            AND checkpoint_ns = ''
            AND channel = 'messages'
            ORDER BY version DESC
            LIMIT 1;
        """, (thread_id,))
        
        result = cur.fetchone()
        
        if result:
            messages_outer = msgpack.unpackb(result[0], raw=False)
            loaded_messages = []
            
            for ext_msg in messages_outer:
                msg_data = msgpack.unpackb(ext_msg.data, raw=False)
                
                # msg_data is in format: [module, class_name, dict, method]
                if len(msg_data) >= 3:
                    msg_dict = msg_data[2]
                    msg_type = msg_data[0]
                    
                    if "human" in msg_type.lower():
                        loaded_messages.append({
                            "role": "user",
                            "content": msg_dict.get("content", "")
                        })
                    elif "ai" in msg_type.lower():
                        loaded_messages.append({
                            "role": "assistant",
                            "content": msg_dict.get("content", "")
                        })
            
            return loaded_messages
    return []

def get_thread_preview(thread_id):
    """Get first message of thread for preview"""
    messages = load_messages_from_checkpoint(thread_id)
    if messages:
        preview = messages[0]["content"][:50]
        return f"{preview}..." if len(messages[0]["content"]) > 50 else preview
    return "Empty chat"

# ==============================================================================
# 5. SIDEBAR - CHAT HISTORY
# ==============================================================================

st.sidebar.markdown("# 💬 Chats")

# Add "New Chat" button at the top
if st.sidebar.button("➕ New Chat", use_container_width=True):
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.messages = []
    st.session_state.awaiting_approval = False
    st.session_state.approval_data = None
    st.rerun()

st.sidebar.markdown("---")

# Load all threads
threads = checkpoints_loaded()

# Display current thread if it has messages but isn't saved yet
if st.session_state.messages and st.session_state.thread_id not in [t[0] for t in threads]:
    preview = st.session_state.messages[0]["content"][:50]
    preview = f"{preview}..." if len(st.session_state.messages[0]["content"]) > 50 else preview
    st.sidebar.button(f"🟢 {preview} (Current)", key="current_thread", use_container_width=True, disabled=True)
    st.sidebar.markdown("---")

# Display each thread as a button
for thread_id, _ in threads:
    preview = get_thread_preview(thread_id)
    
    # Highlight the current thread
    is_current = st.session_state.get("thread_id") == thread_id
    button_label = f"{'🟢' if is_current else '🧵'} {preview}"
    
    if st.sidebar.button(button_label, key=f"thread_{thread_id}", use_container_width=True):
        # Load this thread
        st.session_state.thread_id = thread_id
        st.session_state.messages = load_messages_from_checkpoint(thread_id)
        st.session_state.awaiting_approval = False
        st.session_state.approval_data = None
        st.rerun()

# ==============================================================================
# 6. MAIN UI
# ==============================================================================

st.markdown(
    """
    <style>
        .headhunter-container {
            display: flex;
            justify-content: center;
            align-items: flex-start;
        }

        .headhunter-title {
            font-size: 44px;
            font-weight: 630;
            color: #0077B5;
        }
    </style>

    <div class="headhunter-container">
        <div class="headhunter-title">Agent HeadHunter</div>
    </div>
    """,
    unsafe_allow_html=True
)

# ==============================================================================
# 7. CORE AGENT RUNNER
# ==============================================================================

def run_agent_graph(user_input=None, resume_value=None):
    """
    Connects to the DB, compiles the graph, and runs one turn of the agent.
    """
    config = {
        "configurable": {
            "user_id": "STREAMLIT_USER", 
            "thread_id": st.session_state.thread_id
        }
    }
    
    with PostgresStore.from_conn_string(DB_URI) as store, PostgresSaver.from_conn_string(DB_URI) as checkpointer:
        store.setup()
        checkpointer.setup()
        
        graph = builder.compile(store=store, checkpointer=checkpointer)
        
        try:
            if resume_value:
                command = Command(resume=resume_value)
                events = graph.stream(command, config=config)
            else:
                command = {"messages": [HumanMessage(content=user_input)]}
                events = graph.stream(command, config=config)

            for event in events:
                pass 
                
            snapshot = graph.get_state(config)
            return snapshot
            
        except Exception as e:
            st.error(f"❌ Execution Error: {e}")
            return None

# ==============================================================================
# 8. DISPLAY CHAT
# ==============================================================================

# Display Chat History
for msg in st.session_state.messages:
    role = msg["role"]
    with st.chat_message(role):
        st.markdown(msg["content"])

# Handle Interrupts (The "Payment" Buttons)
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

# Chat Input (Only show if not waiting for approval)
elif prompt := st.chat_input("Ex: Find AI Engineer jobs in Dubai"):
    
    # Add User Message to History
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # Run the Agent
    with st.spinner("Thinking..."):
        snapshot = run_agent_graph(user_input=prompt)

    # Handle the Result
    if snapshot:
        if snapshot.next and snapshot.tasks[0].interrupts:
            interrupt_value = snapshot.tasks[0].interrupts[0].value
            st.session_state.awaiting_approval = True
            st.session_state.approval_data = interrupt_value
            st.rerun()
            
        elif snapshot.values['messages']:
            last_msg = snapshot.values['messages'][-1].content
            st.session_state.messages.append({"role": "assistant", "content": last_msg})
            st.rerun()