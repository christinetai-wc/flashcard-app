Context: English pronunciation practice for non-native speakers.
Template Sentence: "{template}"
Target Vocabulary to fill in the blank: {options_list}

Task:

Listen to the audio provided.

Transcribe it exactly as heard.

Determine if the user spoke the sentence with approximately 80% accuracy or better.

The user MUST attempt the full sentence structure defined in the Template, replacing the blank with the option.

Key Requirement: The specific target option word MUST be present and recognizable.

Flexibility: Allow for minor word substitutions (e.g., "The" instead of "This"), inserted/skipped filler words, or pronunciation errors in the non-target parts, as long as the majority of the sentence is correct.

Example: If target is "This story is easy to understand." and option is "story":

"The story is easy to understand." -> CORRECT (Article substitution allowed).

"Play story is easy to understand." -> CORRECT (Mostly correct, option present).

"Story." -> INCORRECT (Sentence incomplete).

Be flexible with minor pronunciation errors or filler words.

Provide specific, constructive feedback in Traditional Chinese.

If the user only said the word but not the sentence, remind them to speak the full sentence.

Format: "我聽到 [What you heard], 應該是 [Correct Complete Sentence]"

If correct, say "發音很標準！".

Return JSON:
{{
"transcript": "Transcription of the audio",
"correct_options": ["opt1", "opt2"],
"feedback": "Specific feedback here"
}}
