# Application Infrastructure Diagram - Simplified View

## Main Application Flow

```mermaid
flowchart TD
    Start([User/Client]) --> API[FastAPI API Server<br/>Port 8002]
    
    API -->|1. Receives Request| Queue[Redis Queue<br/>Task Manager]
    Queue -->|2. Distributes Tasks| Workers[Celery Workers<br/>Background Processing]
    
    Workers -->|3a. Process Documents| DocFlow{Document Processing}
    Workers -->|3b. Handle Chat| ChatFlow{Chat Processing}
    
    DocFlow -->|Upload| Storage[MinIO<br/>File Storage]
    DocFlow -->|Parse| Parser[LlamaIndex<br/>Document Parser]
    DocFlow -->|Chunk & Embed| AI[Azure OpenAI<br/>Embeddings]
    DocFlow -->|Store Vectors| VectorDB[ChromaDB<br/>Vector Search]
    DocFlow -->|Index Metadata| SearchDB[Elasticsearch<br/>Document Index]
    
    ChatFlow -->|Get Context| Memory[Mem0<br/>User Memory]
    ChatFlow -->|Search Similar| VectorDB
    ChatFlow -->|Generate Response| AI
    ChatFlow -->|Save History| Database[(PostgreSQL<br/>Chat History)]
    
    API -->|Query Results| VectorDB
    API -->|Query Results| SearchDB
    API -->|Query Results| Database
    
    End([Response to User])
    API --> End
    
    style Start fill:#e1f5ff
    style End fill:#e1f5ff
    style API fill:#4a90e2,color:#fff
    style Queue fill:#50c878,color:#fff
    style Workers fill:#50c878,color:#fff
    style VectorDB fill:#ff6b6b,color:#fff
    style SearchDB fill:#ff6b6b,color:#fff
    style Storage fill:#ff6b6b,color:#fff
    style Database fill:#9b59b6,color:#fff
    style AI fill:#ffa500,color:#fff
    style Memory fill:#9b59b6,color:#fff
```

## System Architecture - Component View

```mermaid
graph LR
    subgraph "🌐 Frontend & Clients"
        A[Web Client]
        B[Next.js App]
    end
    
    subgraph "⚡ Application Layer"
        C[FastAPI<br/>REST API]
        D[Celery Workers<br/>7 Workers]
        E[Redis<br/>Task Queue]
    end
    
    subgraph "💾 Data Storage"
        F[ChromaDB<br/>Vectors]
        G[Elasticsearch<br/>Documents]
        H[MinIO<br/>Files]
        I[PostgreSQL<br/>Chat Data]
    end
    
    subgraph "🤖 AI Services"
        J[Azure OpenAI<br/>LLM & Embeddings]
        K[OpenAI<br/>Fallback]
        L[Gemini<br/>Optional]
    end
    
    subgraph "🔧 Support Services"
        M[Langfuse<br/>Prompts]
        N[Mem0<br/>Memory]
        O[LlamaIndex<br/>Parsing]
    end
    
    A --> C
    B --> C
    C --> E
    E --> D
    D --> F
    D --> G
    D --> H
    D --> I
    D --> J
    D --> K
    D --> L
    D --> M
    D --> N
    D --> O
    C --> F
    C --> G
    C --> I
    
    style C fill:#4a90e2,color:#fff
    style D fill:#50c878,color:#fff
    style E fill:#50c878,color:#fff
    style F fill:#ff6b6b,color:#fff
    style G fill:#ff6b6b,color:#fff
    style H fill:#ff6b6b,color:#fff
    style I fill:#9b59b6,color:#fff
    style J fill:#ffa500,color:#fff
    style K fill:#ffa500,color:#fff
    style L fill:#ffa500,color:#fff
```

## How It Works - Step by Step

### 📄 Document Processing Pipeline

```
1. User uploads document
   ↓
2. FastAPI receives request
   ↓
3. Task sent to Redis queue
   ↓
4. Celery worker picks up task
   ↓
5. Document stored in MinIO
   ↓
6. Document parsed by LlamaIndex
   ↓
7. Text split into chunks
   ↓
8. Chunks converted to vectors (Azure OpenAI)
   ↓
9. Vectors stored in ChromaDB
   ↓
10. Metadata indexed in Elasticsearch
   ↓
11. Document ready for search!
```

### 💬 Chat/Query Pipeline

```
1. User asks a question
   ↓
2. FastAPI receives query
   ↓
3. System searches ChromaDB for similar content
   ↓
4. Retrieves relevant document chunks
   ↓
5. Gets user context from Mem0
   ↓
6. Fetches chat history from PostgreSQL
   ↓
7. Builds prompt with context
   ↓
8. Sends to Azure OpenAI for response
   ↓
9. Response saved to PostgreSQL
   ↓
10. Response returned to user
```

## Technology Stack Summary

| Category | Technology | Purpose |
|----------|-----------|---------|
| **API Framework** | FastAPI + Uvicorn | REST API server |
| **Task Queue** | Redis + Celery | Background job processing |
| **Vector Database** | ChromaDB | Store and search embeddings |
| **Document Search** | Elasticsearch | Index and search documents |
| **File Storage** | MinIO | Store uploaded files |
| **Database** | PostgreSQL | Chat history, user data |
| **AI Models** | Azure OpenAI / OpenAI / Gemini | LLM and embeddings |
| **Document Parsing** | LlamaIndex | Convert documents to text |
| **Prompt Management** | Langfuse | Manage and version prompts |
| **Memory** | Mem0 | Store user context and preferences |
| **Orchestration** | LangChain / LangGraph | Workflow management |

## Port Reference

| Service | Port | Access |
|---------|------|--------|
| FastAPI | 8002 | http://localhost:8002 |
| ChromaDB | 8001 | http://localhost:8001 |
| MinIO API | 9000 | http://localhost:9000 |
| MinIO Console | 9001 | http://localhost:9001 |
| Elasticsearch | 9200 | http://localhost:9200 |
| Kibana | 5601 | http://localhost:5601 |
| Flower | 5555 | http://localhost:5555 |
| Redis | 6379 | Internal only |







