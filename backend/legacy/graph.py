# from idlelib.undo import Command

from langgraph.types import Command
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from ingest import SupportTicketRAG
import time

# Initialize the RAG system
rag = SupportTicketRAG(
    dataset_path="./qa_dataset.json",
    k=3,
    persist_directory="."
)

# Ingest the dataset once
rag.ingest()

# Build the LangGraph workflow
app = rag.build_graph(
    checkpointer=MemorySaver()
)

# Generate Mermaid diagram
png_data = app.get_graph().draw_mermaid_png()
with open("langgraph_flow.png", "wb") as f:
    f.write(png_data)
print("Graph image saved!")


import uuid
thread_id = str(uuid.uuid4())
# Use the same thread_id to preserve conversation history.
config = {
    "configurable": {
        "thread_id": thread_id,
    },
    "recursion_limit": 3,
}

questions = [
    "Camera light comes on but it stays black",
    "I want to create a ticket abhi2@example.com",
    "How do I fix it?",
    "What about Android VPN credentials not working?",
    "How to make pizza?",
    "How to make a burger?",
    "How often is the company policy reviewed and updated?",
    "Will I be informed if there are major changes to the company policy?",
    "Any reason why the company policy is reviewed annually?",
    "Experiencing crashes on some machines. I want to raise a ticket",
    "Please assist with setting up Google Calendar"
]

# questions = [
#     "Camera light comes on but it stays black",
# ]
print("#" * 50)
for question in questions:
    print(f"\nUser: {question}")
    start_time = time.perf_counter()
    result = app.invoke(
        {
            "messages": [
                HumanMessage(content=question)
            ]
        },
        config=config,
    )
    # print("Interrupt:", result["__interrupt__"])
    # result = app.invoke(Command(resume="approve"), config=config)
    # Step 2: Handle interruptions
    while result.get("__interrupt__"):

        for interruption in result["__interrupt__"]:

            print("\nHuman approval required:")

            interrupt_data = interruption.value

            # print(interrupt_data["message"])

            # Get actual human decision
            decision = input(
                "Enter your decision (approve/reject): "
            ).strip().lower()

            while decision not in ["approve", "reject"]:
                decision = input(
                    "Please enter approve or reject: "
                ).strip().lower()

            # Step 3: Resume graph execution
            result = app.invoke(
                Command(resume=decision),
                config=config
            )

    end_time = time.perf_counter()
    print(f"Assistant: {result['messages'][-1].content} Response Time: {end_time - start_time:.4f} seconds")
    print("#" * 350)
    # print(f"Assistant: {result['conversation_history']}")
