# Blog 19.02.26

## Migraine, Stress, and the Search for the Needle in the Haystack.

It's an interesting situation. On one hand, cool comments keep trickling in here and there:

> **Ozz** - *Feb 18*
>
> This is absurdly cool! Fast as lightning. Managed to build a macOS version of it in an hour :) THANKS! I'm sure this is an idea that will not go back to the bag. would be cool to see how this gets integrated to "everything"... but for now it makes claude code so much smarter.
>
> THANKS! :)
>
> *(Dev.to comment)*

On the other hand, it feels like nothing is moving. 100 downloads. A few hundred reads on the papers, but it almost seems like a collective "this is interesting, but what do we do with it now?"

Maybe it's simply because the real applications are still missing. DirectShell promises progress, but that only becomes tangible once the first applications built on top of DS actually work in practice.

I'm personally focusing on the AI agent use case because I believe it's one of the most promising — but you also quickly realize you're suddenly in a position where you're building something with no reference. No "let me just Google how to do this." No best practices. Pure trial and error.

And I can tell you: it's exhausting.

## Where do I stand?

The Gemini approach that chunked some of the raw data into "bite-sized pieces" — I scrapped it. Thought it was clever yesterday, realized today: No, I want deterministic solutions even if they're harder.

So I've now built a mechanism into the MCP server that pulls the relevant data from the browser's CDP port, processes it, and dynamically maps the necessary tools from that.

So: No additional API call. No extra cost. That's good.

The biggest unsolved question I'm still facing is how to make AI learning progress flow directly into a learning loop. The problem is clear — sure, you can tell the AI "remember this." But that's not reliable.

There needs to be a method that autonomously, deterministically, and reliably extracts learnings and feeds them back into the AI as permanent loop context.

Other than that, there have been some cool DMs and inquiries. A few meetings are planned. But nothing concrete yet.

With that — have a wonderful Thursday <3 yesterday's post should be floating around somewhere already.
