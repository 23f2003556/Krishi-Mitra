// script.js
// Hindi Voice Assistant UI Logic
// All UI text and speech is in Hindi. No English appears.

document.addEventListener('DOMContentLoaded', () => {
  const chatBox = document.getElementById('chatBox');
  const inputArea = document.getElementById('inputArea');
  const spinner = document.getElementById('spinner');
  const userInput = document.getElementById('userInput');
  const sendBtn = document.getElementById('sendBtn');

  // Helper to speak Hindi text
  function speakHindi(text) {
    const utter = new SpeechSynthesisUtterance(text);
    // Prefer Hindi voice if available
    const voices = window.speechSynthesis.getVoices();
    const hiVoice = voices.find(v => v.lang.startsWith('hi')) || null;
    if (hiVoice) utter.voice = hiVoice;
    utter.lang = 'hi-IN';
    window.speechSynthesis.speak(utter);
  }

  // Helper to add a message bubble
  function addMessage(content, sender) {
    const msg = document.createElement('div');
    msg.className = `message ${sender}`;
    msg.textContent = content;
    chatBox.appendChild(msg);
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  // Initial waiting flow
  spinner.style.visibility = 'visible';
  speakHindi('मैं जाँच रहा हूँ, कृपया प्रतीक्षा करें।');
  // Simulate checking (2 seconds)
  setTimeout(() => {
    spinner.style.visibility = 'hidden';
    const capabilityMsg = 'मैं कई चीज़ों में मदद कर सकता हूँ: जानकारी खोजना, सवालों के जवाब देना, और अधिक।';
    addMessage(capabilityMsg, 'bot');
    speakHindi(capabilityMsg);
    inputArea.hidden = false;
    userInput.focus();
  }, 2000);

  // Send handler
  function handleSend() {
    const query = userInput.value.trim();
    if (!query) return;
    addMessage(query, 'user');
    userInput.value = '';
    // Mock static answer
    const answer = 'यहाँ आपका उत्तर है।';
    addMessage(answer, 'bot');
    speakHindi(answer);
  }

  sendBtn.addEventListener('click', handleSend);
  userInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSend();
    }
  });
});
