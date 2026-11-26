import unittest

from overlay.claude.prompts import FOLLOWUPS, build_context, build_user_content
from overlay.speech.stt import clean_text, glossary_terms


class CleanTextTests(unittest.TestCase):
    def test_hallucinations(self):
        self.assertEqual(clean_text("Продолжение следует..."), "")
        self.assertEqual(clean_text("Субтитры сделал DimaTorzok"), "")
        self.assertEqual(clean_text("да да да да да да да да да да"), "")
        self.assertEqual(clean_text("  Чем   процесс отличается? "), "Чем процесс отличается?")

    def test_hallucination_phrases_with_variations(self):
        self.assertEqual(clean_text("Редактор субтитров А.Синецкая Корректор А.Егорова"), "")
        self.assertEqual(clean_text("Редактор субтитров А. Синецкая Корректор А. Егорова"), "")
        self.assertEqual(clean_text("Ставьте лайк, подписывайтесь на канал и нажимайте на колокольчик!"), "")
        self.assertEqual(clean_text("Subtitles by the Amara.org community"), "")

    def test_real_speech_mentioning_hallucination_words_is_kept(self):
        question = "Как бы вы спроектировали сервис субтитров для видеоплатформы?"
        self.assertEqual(clean_text(question), question)
        self.assertEqual(clean_text("Что такое субтитры?"), "Что такое субтитры?")
        statement = "Мы добавили субтитры к видео, и теперь их можно включить в настройках."
        self.assertEqual(clean_text(statement), statement)

    def test_only_the_invented_sentence_is_dropped(self):
        text = "Thanks for watching the last part. Now, how does garbage collection work in Python?"
        self.assertEqual(clean_text(text), "Now, how does garbage collection work in Python?")
        question = "А теперь вопрос: что такое индекс?"
        self.assertEqual(clean_text(f"Спасибо за просмотр! {question}"), question)

    def test_prompt_echo(self):
        self.assertEqual(clean_text("Собеседование Python.", "Собеседование Python. Привет"), "")

    def test_short_phrase_of_glossary_terms_is_kept(self):
        hotwords = "Kubernetes, PostgreSQL, Django, Redis, Kafka, Celery"
        self.assertEqual(clean_text("PostgreSQL, Django", "", hotwords), "PostgreSQL, Django")

    def test_glossary_terms(self):
        self.assertEqual(glossary_terms(" Kubernetes,PostgreSQL;\nDjango ,, "), "Kubernetes, PostgreSQL, Django")


class PromptTests(unittest.TestCase):
    def test_content_shape(self):
        content = build_user_content(
            transcript="[10:00:00] Как дела?",
            image_b64="AAAA",
            context=build_context("Interview", "Django, Redis"),
            answer_language="uk",
        )
        self.assertEqual(content[0]["type"], "image")
        text = content[1]["text"]
        self.assertIn("<context>\nTopic: Interview\nTerms: Django, Redis\n</context>", text)
        self.assertIn("<transcript", text)
        self.assertIn("Reply in Ukrainian.", text)

    def test_context_omitted_when_unchanged(self):
        content = build_user_content(typed="Hi", context=None)
        self.assertEqual(len(content), 1)
        self.assertNotIn("<context>", content[0]["text"])
        self.assertIn("<typed_question>", content[0]["text"])

    def test_cleared_context(self):
        self.assertIn("no topic set", build_user_content(typed="Hi", context="")[0]["text"])

    def test_followups(self):
        self.assertEqual(set(FOLLOWUPS), {"shorter", "more", "deeper"})


if __name__ == "__main__":
    unittest.main()
