# RAG in ResolveDesk, Explained Simply

Imagine you are helping someone at a bank, but you do not know every answer by heart. Instead of guessing, you look through a carefully prepared book of help articles and show the associate the pages that seem useful. That is the main idea behind RAG in ResolveDesk.

RAG stands for **Retrieval-Augmented Generation**. In this app, the most important part is **retrieval**: finding useful information. The resolution instructions shown to the associate come from the knowledge articles. The app does not ask a chatbot to invent those instructions.

## The Library Analogy

- The **knowledge base** is the library of support articles.
- An **article** is one book about a problem.
- A **chunk** is a small card describing either the whole article or one individual step.
- An **embedding** is a list of numbers that helps the computer compare the meaning of words.
- **Chroma** is the computer's filing cabinet for those cards and number-lists.
- The **search query** is the question the associate needs help with.

## How an Article Gets Ready for Search

Suppose one help article is called **Customer profile does not match submitted details**. It has a short summary and four steps.

ResolveDesk makes five search cards from it:

1. One **overview card** with the article title, summary, category, and source ID.
2. One card for step 1.
3. One card for step 2.
4. One card for step 3.
5. One card for step 4.

Why make small cards? A question may be about just one instruction. A card for that step can match the question more closely than a card containing the whole article.

Each card keeps the article's source ID, so when a step matches, the app can still show the whole article and tell the associate where the guidance came from.

## How the Computer Finds a Match

1. A support article is checked and cleaned. Common emails, long number strings, and phone-like numbers are masked.
2. The article is split into its overview and step cards.
3. A Sentence Transformers model turns the words on each card into numbers called an **embedding**. Similar meanings tend to have number-lists that are close together.
4. Chroma stores the cards, their number-lists, and their labels on this computer.
5. When an application error happens, the associate clicks **Get resolution guidance**. ResolveDesk cleans the error title and description and turns them into a search query.
6. The same model turns that query into numbers. Chroma finds nearby cards.
7. ResolveDesk groups the matching cards by article. If step cards matched, it shows those steps. If only the overview matched, it can show all the article's steps.
8. The app leaves out matches that score below its configured threshold and keeps the results within a limit for number of articles and context tokens.
9. The associate sees the article ID and guidance, then decides what to do.

## A Tiny Example

A payment error might say:

> The payment was declined, but an authorisation is still pending.

The search can find a card from **KB-1042: Card payment declined after verification**. ResolveDesk then shows the article's original instructions, such as checking for a pending authorisation before trying the payment again.

The app does not decide whether the payment should be retried. The associate reads the source and makes that decision.

## Where Jira Fits

A reviewer can approve a finished Jira issue by adding the `kb-approved` label. The issue also needs a comment with a root cause and a resolution, for example:

```text
Root cause: The validation service timed out.
Resolution:
- Check the validation status
- Retry validation once
```

The sync turns that into another knowledge article, with an ID like `KB-JIRA-MC-4`. It joins the same library as the regular articles, gets split into cards, and is indexed in Chroma. The same search can then find it.

## Is a Chatbot Writing the Instructions?

No, not in the resolution-guidance path. The app retrieves the support article and shows its source and steps. This makes it easier for an associate to check where the advice came from.

There is a separate, optional feature that can help draft a Jira bug report. That is different from finding resolution guidance, and it only runs when the report-drafting step is requested.

## Important Things to Remember

- Search is a helpful finder, not a truth machine. Similar wording does not prove that an article is correct for every situation.
- The associate reviews the guidance and chooses what to do.
- If no article is a close enough match, the app can show no guidance rather than pretend it found the answer.
- Jira articles need the approval label and the right comment format before they can be added.
- Embeddings and the Chroma index run locally in this prototype. Jira is contacted when syncing Jira articles.

## One-Line Summary

**RAG helps ResolveDesk find and show the most relevant approved support article, so the associate can use its real steps instead of relying on a guess.**
