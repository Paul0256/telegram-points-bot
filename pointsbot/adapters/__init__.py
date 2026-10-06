"""Messaging adapters: thin translators between a platform and the core.

An adapter's whole job:

1. Turn a platform event into `IncomingCommand` / `IncomingAction`
   (sender `Identity`, a stable `event_id`, command name and arguments).
2. Call `CommandProcessor.handle_command` / `handle_action` (in a worker thread
   if the adapter is async: the core does blocking Google API calls).
3. Render the returned `Response` (rich text + buttons) in the platform's format.

Adapters contain no business rules. See README "How to add another platform".
"""
