# Data Schema

This is a notes and initial thoughts on database table and data organization. This will be used by the coding agents to lay out the actual schema and supporting interface etc. as directed by the user. The tables are described like a class, but not really following strict pythonic model. 

This is a living document, intially updates will be frequent.

# 1. Tables

anything like agentTbl, sessionTbl etc are expected to be on DB. There will be multiple data that will be constructed as objects and then commited to DB. 

## 1.1 Session related tables



Session
```python
Class SessionTbl:
    producer_id: str 
    session_id: str # Session_id of the 
    org_id: str # Organization ID -> points organization table, could be NULL
    user_id: str 
    parent_id: str
    seq: INT # Seq number represents blocks of of Session, starting with 1
    schema_version: str # Used to understand metadata blob, comes from event msg
    agent_id: str # Can't be NULL. agent : claude, codex -> points to vendor table
    created_ts: datetime
    signal_blob: Dict[str, Any] # JSONB blob 
    info_blob: Dict[str, Any] # For Debug and other info


```
## 1.2 Agent, User, Org, Producer Table**



Agent_id must point to valid entry and can't be NULL in SessionTbl

The Other tables will evolve over time. Here is an attempt of what we know today. 

```python
# Typically created during registration if we consider one user per producer.
# That may not be true always
Class UserTbl:
    user_id: str # PK = Primary Key
    email: str
    name: str
    github_user: str
    info: Dict[str, Any]
    created_at: datetime

Class OrgTbl:
    org_id: str # PK
    name: str
    info: Dict[str, Any]
    created_at: datetime

# Contains Producer: typically lapttop or endpoint info
Class ProducerTbl:
    producer_id: str # PK
    ip_address: str #
    name: str
    info: Dict[str, Any]
    created_at: datetime

# Contains info about the coding agent
Class AgentTbl:
    agent_id: str # PK
    agent_desc: str
    ver: str
    info: Dict[str, Any]
    created_at: datetime


```

## 1.3 SessionInfo

The central economics goal is to measure ROI of coding agent - to do that we need to track and measure production. The first thing is to track production for each session and have insights into usage and patterns of each session 

```python
Class modelsTbl:
    id: INT
    model_name: str # Moden name, acts as identification
    info: Dist[str, Any]

# These are going to be cotructed in memory, not committed in memory
Class skill:
    skill_name: str
    tokens: INT
    started: datetime
    ended: datetime


# File hunk, whatever lines added etc. Details are not clear to me yet. 
# Question: Are these present in claude JSONL file when they edit. Lines added, deleted etc.
Class hunk:

Class File:
    name: str
    filetype: str # PPTX, code etc. WIll evolve 
    hunks: List[hunk]
    info: Dict[str, Any]

# Tool: identify and create a list of tools. Evolvng
Class Tool:
    tool_name: str
    # The type of tool will evolve over time
    tool_type: Literal["EDIT", "READ", "WRITE", "SEARCH", "WEB_SRCH", "MCP", "RUN", "LIST", "OTHERS" ]
    # Actual tool cmd with param
    cmd: str

Class subagent:
    desc: str # A short task description
    model: model_name
    tokens: INT
    tools: List[Tool]
    files: List[File]
    started: datetime
    ended: datetime

# Placeholder for now
Class Task:
    desc: str


Class SessionInfoTbl:
    session_id: str
    cost: str # Total token cost, normalized in $$
    started: datetime 
    ended: datetime
    models: List[Dict[model, tokens:INT]]
    skills: List[skills]
    subagents: List[subagent]
    Pr_list: List[Pr_ids] # List of Pull Ruquest attributed to this session. More Details to follow
    Files: List[File]
    tools: List[Tool]
    #
    # Task intent: Early thoghts so this would be a placeholder
    tasks: List[Task]

    info: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    
```

## 1.4 Goods Table

Goods produced by coding agent can be a varied,
1. Code files produced and Pull request produced. It could be straight commits and push but
   teams generally create PR. Each PR have multiple files and goes thru review, fix and accept. Can also be reopened
2. Files: Cowork producing WordDoc or PPTX file
3. Might be fixing infra issues after DEBUG and fix jira/git issues. for example Codex acting as 
   SRE agent, looking at logs and have access to aws commandsets. It may be producing a set of fixes to run. Question: Wht is the output here?

   The goods table, especially the PR Table needs more thought, TBD.

