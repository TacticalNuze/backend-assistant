    # Application Infrastructure Diagram

    ```mermaid
    graph TB
        subgraph "Client Layer"
            Client[Client Application]
            NextJS[Next.js Frontend<br/>Webhook Notifications]
        end

        subgraph "Docker Container Orchestration"
            subgraph "API Layer"
                FastAPI[FastAPI Application<br/>Port 8002<br/>Uvicorn Server]
            end

            subgraph "Task Processing Layer"
                CeleryWorkers[Celery Workers<br/>7 Workers, 3 Queues<br/>- default_queue<br/>- localAPI_queue<br/>- io_queue]
                Redis[Redis<br/>Message Broker &<br/>Result Backend<br/>Port 6379]
                Flower[Flower<br/>Celery Monitoring<br/>Port 5555]
            end

            subgraph "Vector & Search Services"
                ChromaDB[ChromaDB<br/>Vector Database<br/>Port 8001]
                Elasticsearch[Elasticsearch<br/>Document Indexing<br/>Port 9200]
                Kibana[Kibana<br/>Elasticsearch UI<br/>Port 5601]
            end

            subgraph "Storage Services"
                MinIO[MinIO<br/>Object Storage<br/>Ports 9000/9001]
            end
        end

        subgraph "External APIs & Services"
            AzureOpenAI[Azure OpenAI API<br/>- LLM Models<br/>- Embeddings]
            OpenAI[OpenAI API<br/>Fallback Provider]
            Gemini[Google Gemini API<br/>Optional Fallback]
            LlamaIndex[LlamaIndex Cloud API<br/>Document Parsing]
        end

        subgraph "LLMOps & Prompt Management"
            Langfuse[Langfuse<br/>Prompt Store<br/>LLMOps & Versioning]
        end

        subgraph "Database Services"
            PostgreSQL[(PostgreSQL/Supabase<br/>Chat History<br/>User Data<br/>RBAC)]
        end

        subgraph "Memory & Context Services"
            Mem0[Mem0<br/>User Memory<br/>Conversation Context<br/>User Preferences]
        end

        subgraph "Application Libraries"
            LangChain[LangChain<br/>Orchestration<br/>Prompt Management]
            LangGraph[LangGraph<br/>Workflow Management<br/>State Handling]
        end

        %% Client connections
        Client -->|HTTP Requests| FastAPI
        NextJS -->|Webhooks| FastAPI
        FastAPI -->|Notifications| NextJS

        %% API to Task Processing
        FastAPI -->|Enqueue Tasks| Redis
        Redis -->|Task Distribution| CeleryWorkers
        CeleryWorkers -->|Task Results| Redis
        Redis -->|Status Updates| Flower
        FastAPI -->|Query Status| Redis

        %% Task Processing to Storage
        CeleryWorkers -->|Store Vectors| ChromaDB
        CeleryWorkers -->|Index Documents| Elasticsearch
        CeleryWorkers -->|Store Files| MinIO
        CeleryWorkers -->|Save Chat History| PostgreSQL
        CeleryWorkers -->|Store Memories| Mem0

        %% Task Processing to External APIs
        CeleryWorkers -->|LLM Calls| AzureOpenAI
        CeleryWorkers -->|LLM Calls| OpenAI
        CeleryWorkers -->|LLM Calls| Gemini
        CeleryWorkers -->|Generate Embeddings| AzureOpenAI
        CeleryWorkers -->|Parse Documents| LlamaIndex
        CeleryWorkers -->|Fetch Prompts| Langfuse

        %% API to Services
        FastAPI -->|Query Vectors| ChromaDB
        FastAPI -->|Search Documents| Elasticsearch
        FastAPI -->|Retrieve Files| MinIO
        FastAPI -->|Query Chat History| PostgreSQL
        FastAPI -->|Fetch User Context| Mem0

        %% Prompt Management Flow
        Langfuse -->|Prompt Templates| CeleryWorkers
        LangChain -->|Orchestration| CeleryWorkers
        LangGraph -->|Workflow State| CeleryWorkers

        %% Data Flow Annotations
        ChromaDB -.->|Metadata Lookup| Elasticsearch
        Elasticsearch -.->|Fallback Reference| ChromaDB

        %% Styling
        classDef apiLayer fill:#4A90E2,stroke:#2E5C8A,stroke-width:2px,color:#fff
        classDef taskLayer fill:#50C878,stroke:#2D8659,stroke-width:2px,color:#fff
        classDef storageLayer fill:#FF6B6B,stroke:#CC5555,stroke-width:2px,color:#fff
        classDef externalLayer fill:#FFA500,stroke:#CC8500,stroke-width:2px,color:#fff
        classDef dbLayer fill:#9B59B6,stroke:#7D3C98,stroke-width:2px,color:#fff
        classDef clientLayer fill:#3498DB,stroke:#2874A6,stroke-width:2px,color:#fff

        class FastAPI apiLayer
        class CeleryWorkers,Redis,Flower taskLayer
        class ChromaDB,Elasticsearch,MinIO,Kibana storageLayer
        class AzureOpenAI,OpenAI,Gemini,LlamaIndex,Langfuse externalLayer
        class PostgreSQL,Mem0 dbLayer
        class Client,NextJS clientLayer
    ```

    ## Architecture Overview

    This diagram illustrates the complete infrastructure of the VectorRAG Backend Pipeline application, showing how all components interact and communicate.

    ### Key Components

    1. **Client Layer**: External applications and Next.js frontend that interact with the API
    2. **API Layer**: FastAPI application serving HTTP requests on port 8002
    3. **Task Processing**: Celery workers with Redis broker handling asynchronous tasks
    4. **Vector & Search**: ChromaDB for vector storage and Elasticsearch for document indexing
    5. **Storage**: MinIO for object storage of files and documents
    6. **External APIs**: Azure OpenAI, OpenAI, Gemini for LLM and embeddings; LlamaIndex for parsing
    7. **LLMOps**: Langfuse for prompt management and versioning
    8. **Databases**: PostgreSQL for chat history and user data
    9. **Memory**: Mem0 for user context and conversation memory
    10. **Libraries**: LangChain and LangGraph for orchestration and workflow management

    ### Data Flows

    - **Request Flow**: Client → FastAPI → Redis → Celery Workers
    - **Document Processing**: MinIO → Parse → Chunk → Embed → ChromaDB + Elasticsearch
    - **Chat Flow**: User Input → Search Vectors → LLM → Save to PostgreSQL
    - **Memory Flow**: User Interactions → Mem0 → Context Retrieval







