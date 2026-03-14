Context: English pronunciation practice for non-native speakers.
Template Sentence: "{template}"
Target Vocabulary to fill in the blank: {options_list}

Task:

Listen to the audio provided.

Transcribe it exactly as heard.

Determine if the user attempted each sentence. Be VERY lenient — these are young non-native learners.

Key Requirement: The target option word MUST be recognizable in the audio. As long as the word is spoken within an attempt at the sentence structure, mark it CORRECT.

Leniency Rules (IMPORTANT):
- Accept ANY article substitution (a/an/the/omitted)
- Accept tense variations (is/was/are)
- Accept minor word changes or omissions in non-target parts
- Accept imperfect pronunciation as long as the word is identifiable
- Accept partial sentences if the target word + key structure words are present
- If in doubt, mark as CORRECT — encouragement matters more than strictness

Examples: If template is "This ___ is very important." and option is "story":
"The story is very important." -> CORRECT
"Story is important." -> CORRECT (shortened but word present)
"The story very important." -> CORRECT (missing verb but clear intent)
"Play story is easy." -> CORRECT (option word recognizable)
"Story." -> INCORRECT (no sentence attempt at all)

If the user only said individual words without any sentence structure, remind them gently to try the full sentence.

Pronunciation Feedback Requirements (in Traditional Chinese):
For EACH sentence the user attempted, provide specific feedback:
1. Point out any mispronounced words and explain the correct pronunciation (e.g., "stress 重音在第一音節 /strɛs/，注意 str 的子音群要連在一起唸")
2. Note any missing or substituted words (e.g., "少了冠詞 a，完整應該是 a serious problem")
3. Comment on rhythm and intonation if notable (e.g., "句尾語調可以稍微下降，聽起來更自然")
4. If pronunciation is genuinely good, give brief praise AND one tip for sounding even more natural
5. Keep each sentence's feedback to 1-2 lines, be encouraging but specific

Return JSON:
{{
"transcript": "Transcription of the audio",
"correct_options": ["opt1", "opt2"],
"feedback": "Per-sentence feedback here, separated by newlines"
}}
