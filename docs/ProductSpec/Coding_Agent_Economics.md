
# The Problem

Coding Agents like Claude, Codex and Cursor are heavily adopted by organization widely.  However rising and runaway cost (Uber consuming the whole years of token in 3 months) are putting CIO/CTO/VPs into a bind.  

The new era of tokenomics demands token efficiency maxing and ROI measurement rather than tokenmaxing.

The problems are:

1. C-level/VP doesn't have visibility into detailed usage of tokens on tasks, sessions, users, projects and such. The usage patterns, behavior is the intelligence is the first ingredient to drive token efficiency. 
2. Customers can not predict or plan usage. 
3. Measure Productivity and ROI or AI usage.  
	1. Code velocity
	2. Quality, size
	3. Waste 
4. Customers are lacking control and governance around cost and efficiency.  
	1. Control sudden spike in cost due to model, abnormal usage and such
	2. Long horizon tasks, loops and goals can create unbounded execution that can lead both unbounded tokens, resources and security risks. 



Individual Developers:
1. Which plan is the most cost effective.  Max, Pro or basic or some combo
2. Is there any waste, where can find savings

## Why Now

There are different forces that are effectively pushing up costs.

- Frontier Models tend to have use more reasoning budget and token. 
- While endless investments have subsidized the cost, that will quickly end as monetization pressure increases.
- More long horizon task. Coding agents are using autonomous [Loops](https://x.com/mvanhorn/article/2063865685558903149) , A recursive, accumulative  approach that can compound context and drain token budgets in a hurry. 
- A plethora of models. Family of models from claude/openAI, open weight models - however the layer that intelligently pairs right model for right task is missing. 

AI has democratized development, everyone, small and big is a builder since they understand the problem space. However these orgs are equipped to manage, govern and ultimately drive up the overall ai efficiency. These orgs has to rely on external vendors.

There X number of companies using AI  and that usage will only go higher. Barring a few large enterprises everyone else will need an external platform to optimize ai usage and increase ROI. 


# Personas

**Buyers:**  
	Person who is feeling the pressure of cost of ai agents.
	- CIO, CTO and C-level exec
	- VP of  Eng/Dev org

**Developers**: 
	They are users and part of the product(solution) installs on their personal laptop.
		- Must have ability to opt-out/ switch off
		- *What values can we provide that benefits devs directly?*  I

## Stories:
I asked around devs about this.
 - Enterprise editions are already collecting data about agent chat, usage etc. In one case of a startup of 8 devs, Cursor is the coding agent. VPE/CTO are feeling the cost pressure but there is no bandwidth for anyone to figure out Why.
 - Devs have no issues to follow any guideline/tools to lower the cost.
 - Devs want to control Model selection but not against auto mode that selects best model. For agentic workflow AUTO mode is default. 
 - The VPE and Sr Eng. is already using goals/loops essentially a long horizon agents that spawns thousands of sub-agents and can drive up the token usage. 

# Solution Spec

Broken down in sections.

## Per Session Cost and Productivity


**Per User/ Per Session**

| RQ                         | Pri     | Desc                                                                                                                                                                                                                                                                                                                                                                                                |
| -------------------------- | ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 01 <br>Session Metric      | 1, MVP  | Cost, Model, Length, Terminal Status <br>Realtime Token budget. Plan depletion, Token budget depletion<br>Sub-agents created, Tools Used, MCP sessions<br>Cache Effeciency. Context Overrun. <br>Work Output. Files (changed, created)                                                                                                                                                              |
| 02<br>Productivity Measure | 1, MVP  | Based on git activity on work output produced in a session<br>PR Opened, Closed, Reviewed, Accepted, Rejected<br>PR re opened<br>Bugs? Not sure how to track bugs against previous PR                                                                                                                                                                                                               |
| 03<br>Session quality      | 1, MVP? | This is quality measurement of the model output. <br>- Measure Rework.  If the work is accepted as is then it is great. More rework, loop is lower quality. This is difficult since most large codes are not reviewed by human but plan is often reviewed.<br>- Measure Plan, rework<br>- How many suggestions are auto accepted<br>- Did it have Eval, Pass/fail <br>- test coverage if applicable |
| 04<br>Task Classification  | 1,2     | Sessions contain various tasks. <br>RESEARCH, DEBUG, FIX, IMPLEMENT_CODE, PLAN,  Misc<br>- Label task, Shosrt desc, Length, Output, Cost<br>- Tools and Models Used<br>- subagent, skills                                                                                                                                                                                                           |

## Enterprise Dashboard

The single pane of view 


## Governance and Control

Under development.
Requires LLM gateway and Talium Policy

| RQ                                   | Pri      | Desc                                                                                                                                  |
| ------------------------------------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| 01<br>Realtime Quota                 | 1<br>Ph2 | Realtime token tracking per user/org based time, project<br> - Hard stop, throttle<br> - ask for approval, for Loop/Long running task |
| 02<br>Model Selection                | 2<br>Ph2 | Auto select Model. User can always override                                                                                           |
| 03<br>Planning and cost<br>budgeting | 2<br>Ph2 |                                                                                                                                       |





# Open Questions:

1. *What value does it provide to a individual developer?*  Devs historically don't like others snooping into the works which could be a friction to adoption. 


