
import json
import os
import sys
from typing import Annotated, TypedDict, List, Dict, Any, Optional

from dotenv import load_dotenv

from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
)
from langchain_core.prompts import (
    ChatPromptTemplate,
    MessagesPlaceholder,
)
from langchain_core.output_parsers import StrOutputParser

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from intent import PolicyCategory, IntentStatus, IntentResult
from typesafe_sdk import Choice, TypeSafeClient
import time
from issue_tracker import IssueTracker

load_dotenv(override=True)


class SupportState(TypedDict, total=False):
    """State passed between LangGraph nodes."""

    # Current user question
    current_question: str

    # Previous conversation messages
    # conversation_history: List[Dict[str, Any]]

    # Automatically maintained conversation messages
    messages: Annotated[List[AnyMessage], add_messages]

    # Previously identified category
    active_category: str | None = None

    # Previously identified intent
    active_intent: str | None = None

    # Action type requested
    action_type: str | None = None

    # Output from intent node
    intent_result: Dict[str, Any]

    raise_ticket: str | None = None

    retrieval_done: bool = False

    # messages: Annotated[list[AnyMessage], add_messages]
    # context: str


class SupportTicketRAG:
    def __init__(
        self,
        dataset_path="./qa_dataset.json",
        collection_name="tier0_collection",
        embedding_model=None,
        model_name=None,
        k=3,
        persist_directory=None,
    ):
        self.dataset_path = dataset_path
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self.k = k
        self.issue_tracker = IssueTracker()

        self.embedding_model = OpenAIEmbeddings(
            model=(
                embedding_model
                or os.getenv(
                    "OPENAI_EMBEDDING_MODEL",
                    "text-embedding-3-small",
                )
            )
        )

        self.llm = ChatOpenAI(
            model=model_name or os.getenv("OPENAI_MODEL_NAME"),
            temperature=0,
            timeout=120,
            max_retries=3,
        )

        self.vector_store = None
        self.retriever = None
        self.graph = None

        self.prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """
You are a support ticket retrieval assistant.

Answer using only the retrieved information.

Instructions:
1. Select the ticket that most directly answers the question.
2. Return the ticket's original answer verbatim.
3. Return its ticket ID and source exactly as provided.
4. Never invent or modify an answer, ticket ID, or source.
5. Return exactly these fields:

If answer found then respond in the following format
answer: answer
ticketid: ticketID
source: source

If answer not found then say "I could not find an answer in the KB. Please raise a ticket" 

Retrieved tickets:
{context}
""",
            ),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{question}"),
        ])

        self.chain = self.prompt | self.llm | StrOutputParser()

    def ingest(self):
        """Load tickets and create the Chroma vector store."""

        self.vector_store = Chroma(
            embedding_function=self.embedding_model,
            collection_name=self.collection_name,
            persist_directory=self.persist_directory,
        )

        # Check whether collection already contains documents
        collection_count = self.vector_store._collection.count()
        # collection_count = self.vector_store._collection.count()

        if collection_count > 0:
            print(
                f"Existing vector store found "
                f"({collection_count} documents). Skipping ingestion."
            )
        else:
            print("No existing documents found. Creating vector store...")

            print("Loading data...")

            with open(self.dataset_path, "r", encoding="utf-8") as f:
                tickets = json.load(f)

            documents = []

            for ticket in tickets:
                content = f"Question: {ticket['question']}"

                documents.append(
                    Document(
                        page_content=content,
                        metadata={
                            "ticketid": str(ticket["ticketid"]),
                            "source": ticket["source"],
                            "category": ticket["category"],
                            "subcategory": ticket["subcategory"],
                            "answer": ticket["answer"],
                        },
                    )
                )

            print(f"Loaded {len(documents)} documents")

            print("Creating vector store...")
            self.vector_store.add_documents(documents)
        self.retriever = self.vector_store.as_retriever(
            search_kwargs={"k": self.k}
        )
        print("Vector store ready")

        return self.vector_store

    def format_docs(self, docs):
        """Format retrieved documents for the LLM."""

        formatted = []

        for doc in docs:
            formatted.append(
                f"""
Question:
{doc.page_content}

Answer:
{doc.metadata.get('answer', 'Unknown')}

Source:
{doc.metadata.get('source', 'Unknown')}

TicketID:
{doc.metadata.get('ticketid', 'Unknown')}

Category:
{doc.metadata.get('category', 'Unknown')}

Subcategory:
{doc.metadata.get('subcategory', 'Unknown')}
""".strip()
            )

        return "\n\n".join(formatted)

    def retrieve(self, question):
        """Retrieve documents for a question."""

        if self.retriever is None:
            raise RuntimeError("Call ingest() before retrieving documents.")

        return self.retriever.invoke(question)

    def answer(self, question, history=None):
        """Standalone conversational answering method."""

        docs = self.retrieve(question)
        context = self.format_docs(docs)

        response = self.chain.invoke({
            "context": context,
            "history": history or [],
            "question": question,
        })

        return response

    def check_intent(self, state: SupportState):
        current_question = state.get("current_question", "")

        with TypeSafeClient() as client:
            response = client.system_one(
                state={"document": f"{current_question}"},
                questions={
                    "category": Choice(
                        instructions="What is this question about?",
                        criteria={"HR Policy": None, "IT Support": None, "Other": None},
                    ),
                },
            )

        print(response.choices["category"].choice)

    def intent_node(self, state: SupportState) -> dict:
        """LangGraph node: classify question intent"""

        # 1. Read conversation state
        # current_question = state.get("current_question", "")
        (current_question, history) = self.find_latest_question(state)
        print(f"\nintent_node... current_question:[{current_question}] history:[{history}]")

        # conversation_history = state.get(
        #     "conversation_history", []
        # )

        # messages = state.get("messages", [])
        #
        # # Convert messages into Jev-compatible context
        # jev_conversation_context = [
        #     {
        #         "role": "user" if msg.type == "human" else "assistant",
        #         "content": msg.content
        #     }
        #     for msg in messages
        # ]

        # Convert messages into Jev-compatible context
        jev_conversation_context = [
            {
                "role": "user",
                "content": history,
            }
        ]

        active_category = state.get("active_category", None)
        active_intent = state.get("active_intent", None)
        active_intent = state.get("active_intent", None)
        # if active_intent == "SWITCH":
        #     print("Conversation switched! Clearing conversation history for JEV...")
        #     jev_conversation_context = ""

        print(f"intent_node... jev_conversation_context:[{jev_conversation_context}]")
        print(f"intent_node... jev_state: CurrentQuestion[{current_question}]")

        # 2. Prepare context for Jev
        jev_state = {
            "current_question": current_question,
            "conversation_history": jev_conversation_context,
            "active_category": active_category,
            "active_intent": active_intent
        }

        # 3. Define typed questions
        questions = {

            "category": Choice(
                instructions="""
                Classify the CURRENT USER QUESTION into
                exactly one of these categories:

                HR Policy:
                Employee benefits, annual leave, sick leave,
                attendance, payroll, compensation, recruitment,
                workplace conduct and HR procedures.

                IT Support:
                Computers, software, hardware, passwords,
                cybersecurity, network access, internet usage
                and information security.

                Not Available:
                Anything unrelated to HR Policies or IT Support.

                Classify the current question, not the
                entire conversation.
                """,
                criteria={
                    "HR Policy": "The question concerns employee or HR policies.",
                    "IT Support": "The question concerns IT Support or technology policies.",
                    "Not Available": "The question is outside HR Policy and IT Support scope."
                }
            ),

            "active_intent": Choice(
                instructions="""
                Determine the intent status of the CURRENT
                USER QUESTION using the conversation history.

                NEW:
                The first question in a conversation, or a
                request with no relevant previous context.

                CONTINUATION:
                The user is following up on the same topic
                or an existing discussion.

                SWITCH:
                The user has clearly changed to a different
                topic or intent, including a change from
                HR Policy to IT Support or vice versa.

                A change of subject within the same category
                can also be a SWITCH.

                Do not assume every follow-up is a continuation.
                """,
                criteria={
                    "NEW": "A new request without relevant previous context.",
                    "CONTINUATION": "A follow-up to the existing topic.",
                    "SWITCH": "The user has changed to a different topic or intent."
                }
            ),

            "action_type": Choice(
                instructions="""
            Classify the CURRENT USER QUESTION into
            exactly one of these categories:

            ANSWER:
            The user wants information

            CREATE TICKET:
            The user wants to create a ticket

            Not Available:
            Anything unrelated to answering or creating tickets.

            Classify the current question, not the
            entire conversation.
            """,
                criteria={
                    "ANSWER": "The user wants information.",
                    "CREATE TICKET": "The user wants to create a ticket",
                    "Not Available": "The user is asking for something outside of information retrieval or ticket creation"
                }
            ),

        }

        # 4. Invoke TypeSafe Jev
        with TypeSafeClient() as client:
            response = client.system_one(
                model="jev-latest",
                state=jev_state,
                questions=questions
            )

        # 5. Extract typed answers
        category_answer = response.choices["category"]
        intent_answer = response.choices["active_intent"]
        action_answer = response.choices["action_type"]

        category = category_answer.choice
        intent = intent_answer.choice
        # intent = "NEW" if intent_answer == "SWITCH" else "SWITCH"
        action_type = action_answer.choice

        # 6. Prepare node output
        result = {
            "active_category": category,
            "active_intent": intent,
            "action_type": action_type,
            "category_confidence": category_answer.confidence,
            "intent_confidence": intent_answer.confidence,
            "ticket_confidence": action_answer.confidence,
            "model": response.model
        }

        print(f"intent_node result: {result}")

        # 7. Return updated LangGraph state
        return {
            "intent_result": result,
            "active_category": category,
            "active_intent": intent,
            # "active_intent": (
            #     current_question if intent in ("NEW", "SWITCH")
            #     else active_intent
            # ),
            "action_type": action_type
        }

    # def intent_node(self, state: SupportState):
    #     """LangGraph node: classify question intent"""
    #
        # messages = state["messages"]
        # print(f"intent_node: {messages}")
        #
        # # Find the latest user question
        # user_indices = [
        #     i for i, message in enumerate(messages)
        #     if isinstance(message, HumanMessage)
        # ]
        #
        # if not user_indices:
        #     raise ValueError("No user question found in graph state.")
        #
        # # Just get the question
        # question = messages[user_indices[-1]].content


    def ticket_node(self, state: SupportState):
        """LangGraph node: create ticket"""
        (current_question, history) = self.find_latest_question(state)
        print(
            f"ticket_node... current_question:[{current_question}]"
            f"history:[{history}]"
        )

        import re
        emails = re.findall(
            r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
            f"{history} {current_question}"
        )
        if len(emails) != 1:
            request_email_address = "Enter your email address"
            print(request_email_address)
            return {
                "messages": [AIMessage(content=request_email_address)],
            }
        email = emails[0]
        first_question = state["messages"][0].content
        print(f"First question: {first_question} Email address: {email}")
        open_tickets = self.issue_tracker.fetch_open_tickets(email)
        raise_ticket = state.get("raise_ticket", None)
        if (raise_ticket is None):
            # if len(open_tickets) > 0:
            print("RAISING INTERRUPT!!")

            # Pause and request human input
            decision = interrupt({
                "messages": [AIMessage(content=f"Here is a list of your open tickets \n{open_tickets}\n" 
                                               "Do you want to create a new ticket?")],
                "options": ["approve", "reject"]
            })
        else:
            decision = raise_ticket

        print("Human decision:", decision)

        if (decision == "approve"):
            ticket = self.issue_tracker.create_new_ticket(email, current_question, current_question)
            return {
                "messages": [AIMessage(content=f"Ticket created: {ticket}")],
                "raise_ticket": None
            }
        return {
            "messages": [AIMessage(content=f"Ticket will not be created!")],
            "raise_ticket": None
        }

    def find_latest_question(self, state: SupportState):
        messages = state["messages"]

        # Find the latest user question.
        user_indices = [
            i for i, message in enumerate(messages)
            if isinstance(message, HumanMessage)
        ]

        if not user_indices:
            raise ValueError("No user question found in graph state.")

        # Get the question and the question index
        question_index = user_indices[-1]
        question = messages[question_index].content
        return (question, messages[:question_index])

    # def find_history(self, state: SupportState):
    #     messages = state["messages"]

    def retrieve_kb_node(self, state: SupportState):
        """LangGraph node: retrieve relevant KB articles."""

        start_time = time.perf_counter()
        if state["active_category"] == "Not Available":
            return {
                "messages": [AIMessage(content="Your question is outside my area of expertise. "
                                               "Please ask a question that belongs to HR Policies or IT Support.")],
            }

        # messages = state["messages"]
        (question, history) = self.find_latest_question(state)
        print(f"retrieve_kb_node: question:{question}")

        docs = self.retrieve(question)
        context = self.format_docs(docs)
        # state["retrieval_done"] = True # Should not update state directly
        # Only use conversation messages before the latest question.
        # history = messages[:question_index]
        if (state["active_intent"] == "SWITCH"):
            history = []
        print(f"retrieve_kb_node: history: {history}")
        # print(f"retrieve_kb_node: context: {context}")

        response = self.chain.invoke({
            "context": context,
            "history": history,
            "question": question,
        })
        end_time = time.perf_counter()
        print (f"retrieve_kb_node: response: {response} Response Time: {end_time - start_time:.4f} seconds")
        return {
            "messages": [AIMessage(content=response)]
        }



    # def answer_node(self, state: SupportState):
    #     """LangGraph node: generate the response."""
    #
    #     messages = state["messages"]
    #     print(f"answer_node: messages: {messages}")
    #
    #     user_indices = [
    #         i for i, message in enumerate(messages)
    #         if isinstance(message, HumanMessage)
    #     ]
    #
    #     if not user_indices:
    #         raise ValueError("No user question found in graph state.")
    #
    #     # Get the question and the question index
    #     question_index = user_indices[-1]
    #     question = messages[question_index].content
    #
    #
    #     # retrieval_done = state.get("retrieval_done", False)
    #
    #     print(f"answer_node: question:{question} history:{history} context:{state.get('context', "")} retrieval_done:{retrieval_done}")
    #
    #     # if retrieval_done:
    #     # response = self.chain.invoke({
    #     #     "context": state.get("context", ""),
    #     #     "history": history,
    #     #     "question": question,
    #     # })
    #     # state["retrieval_done"] = False
    #     return "retrieve_kb_node"
    #     # else:
    #     #     return "retrieve_kb_node"
    #
    #     # print(f"answer_node: response: {response}")
    #     #
    #     # # LangGraph appends this message to the existing conversation.
    #     # return {
    #     #     "messages": [AIMessage(content=response)]
    #     # }

    def routing_function(self, state: SupportState):
        """LangGraph node: routing function."""

        active_category = state["active_category"]
        active_intent = state["active_intent"]
        action_type = state["action_type"]
        category_confidence = state["intent_result"]["category_confidence"]
        ticket_confidence = state["intent_result"]["ticket_confidence"]
        print(f"routing_function... ActiveCategory:{active_category} ActiveIntent:{active_intent}  ActionType:{action_type}")

        if action_type == "Not Available" or action_type == "ANSWER":
            return "retrieve_kb_node"
        if action_type == "CREATE TICKET":
            return "ticket_node"
        return END
        # if active_category == "HR ":
        # if active_category != "Not Available" and active_intent == "NEW":
        #     return "retrieve_kb_node"
        # if active_intent == "SWITCH":
        #     return "intent_node"
        # if ticket_confidence >= 0.99:
        #     return "ticket_node"
        # if action_type == "Not Available":
        #     return END
        # if (active_category == "Not Available" and category_confidence < 0.75) or (active_category != "Not Available"):
        #     print("Invoking answer_node")
        #     return "answer_node"
        # if (active_category == "")

    def route_after_answer(self, state: SupportState):
        if not state.get("context"):
            return "retrieve_kb_node"

        return "intent_node"


    def build_graph(self, checkpointer=None):
        """Build and compile the LangGraph workflow."""

        if self.retriever is None:
            raise RuntimeError("Call ingest() before building the graph.")

        workflow = StateGraph(SupportState)

        workflow.add_node("intent_node", self.intent_node)
        # workflow.add_node("answer_node", self.answer_node)
        workflow.add_node("retrieve_kb_node", self.retrieve_kb_node)
        workflow.add_node("ticket_node", self.ticket_node)
        # workflow.add_node("create_ticket_node", self.create_ticket_node)

        # Test retrieve-answer
        # workflow.add_edge(START, "retrieve")
        # workflow.add_edge("retrieve", "answer")
        # workflow.add_edge("answer", END)

        # Test intent
        # workflow.add_edge(START, "intent_node")
        # workflow.add_edge("intent_node", END)

        # Add conditional edges
        workflow.add_edge(START, "intent_node")
        workflow.add_conditional_edges(
            "intent_node",
           self.routing_function,
           {
               "retrieve_kb_node": "retrieve_kb_node",
               "ticket_node": "ticket_node",
               "intent_node": "intent_node",
               END: END
           }
        )
        # workflow.add_conditional_edges(
        #     "ticket_node",
        #     self.routing_function,
        #     {
        #         "create_ticket_node": "create_ticket_node",
        #         END: END
        #     }
        # )

        # workflow.add_edge("answer_node", END)
        # workflow.add_edge("ticket_node", END)

        # The following yields an infinite loop
        # workflow.add_edge("answer_node", "retrieve_kb_node")
        # workflow.add_edge("retrieve_kb_node", "answer_node")

        # workflow.add_conditional_edges(
        #     "answer_node",
        #     self.route_after_answer
        # )

        # workflow.add_edge("answer_node", "retrieve_kb_node")
        workflow.add_edge("retrieve_kb_node", END)
        workflow.add_edge("ticket_node", END)
        # workflow.add_edge("create_ticket_node", END)

        # # Later uncomment the following 2 lines when Redis Semantic Cache lookup is ready
        # workflow.add_edge("answer", "redis_cache_node")
        # workflow.add_conditional_edges(
        #     "redis_cache_node",
        #     routing_kb_function,
        #     {
        #         "retrieve_kb_node": "retrieve_kb_node",
        #         END: END
        #     })

        self.graph = workflow.compile(
            checkpointer=checkpointer or MemorySaver()
        )

        return self.graph

    @staticmethod
    def calculate_metrics(retrieved_ids, relevant_ids, k=3):
        """Calculate Precision@k, Recall@k, and F1@k."""

        retrieved = retrieved_ids[:k]
        retrieved_set = set(retrieved)
        relevant_set = set(relevant_ids)

        true_positives = len(retrieved_set & relevant_set)

        precision = (
            true_positives / len(retrieved)
            if retrieved else 0.0
        )

        recall = (
            true_positives / len(relevant_set)
            if relevant_set else 0.0
        )

        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall else 0.0
        )

        return {
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
