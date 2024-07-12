import telebot
import feedparser
import json
import os
import re
import time
import threading

bot = telebot.TeleBot("")

# Path to directory for storing scraped links data and user-specific flags
DATA_DIR = "user_data"
try:
    os.makedirs(DATA_DIR)
except Exception:
    pass
FIRST_RUN_FILE_SUFFIX = "_first_run.flag"
RUNNING_THREAD_SUFFIX = "_running.flag"

# Ensure DATA_DIR exists
os.makedirs(DATA_DIR, exist_ok=True)


def log_message(message):
    print(f"[{message.date}] [{message.chat.id}] {message.text}")


@bot.message_handler(commands=["start"])
def handle_start(message):
    chat_id = message.chat.id
    log_message(message)
    bot.send_message(chat_id, "Hello! Enter the RSS feed URL from Upwork:")


@bot.message_handler(commands=["delete", "add", "run", "break_run"])
def handle_operation(message):
    chat_id = message.chat.id
    log_message(message)
    command = message.text.strip().lower()

    if command == "/delete":
        if has_saved_links(chat_id):
            names = get_saved_link_names(chat_id)
            bot.send_message(chat_id, f"Select the RSS feed name to delete:\n{names}")
            bot.register_next_step_handler(message, delete_link_by_name)
        else:
            bot.send_message(chat_id, "No saved RSS feeds to delete.")
            choose_operation(chat_id)

    elif command == "/add":
        bot.send_message(chat_id, "Enter the RSS feed URL from Upwork:")
        bot.register_next_step_handler(message, handle_rss_url)

    elif command == "/run":
        start_periodic_check(chat_id)

    elif command == "/break_run":
        stop_periodic_check(chat_id)


@bot.message_handler(func=lambda message: True)
def handle_rss_url(message):
    chat_id = message.chat.id
    log_message(message)
    rss_url = message.text.strip()

    if rss_url.startswith("http"):
        feed = check_rss(rss_url)
        if feed:
            bot.send_message(chat_id, f"URL '{rss_url}' is a valid Upwork RSS feed.")
            bot.send_message(chat_id, "Please enter a name for this RSS feed:")
            bot.register_next_step_handler(message, add_link_with_name, rss_url)
        else:
            bot.send_message(chat_id,
                             "The provided URL is not a valid Upwork RSS feed. Please enter a correct URL.")
    else:
        bot.send_message(chat_id, "Invalid URL. Please enter the RSS feed URL from Upwork:")


def add_link_with_name(message, rss_url):
    chat_id = message.chat.id
    log_message(message)
    name = message.text.strip()

    # Ensure user data file exists
    if not os.path.exists(f"{DATA_DIR}/{chat_id}.json"):
        with open(f"{DATA_DIR}/{chat_id}.json", 'w', encoding='utf-8') as f:
            json.dump([], f)

    # Load existing links
    with open(f"{DATA_DIR}/{chat_id}.json", 'r', encoding='utf-8') as f:
        scraped_links = json.load(f)

    # Check if the RSS URL is already saved
    if any(link["url"] == rss_url for link in scraped_links):
        bot.send_message(chat_id, f"RSS feed with URL '{rss_url}' is already added.")
        choose_operation(chat_id)
        return

    # Add new link
    scraped_links.append({"name": name, "url": rss_url})

    # Save updated links (keep only the latest 500)
    with open(f"{DATA_DIR}/{chat_id}.json", 'w', encoding='utf-8') as f:
        json.dump(scraped_links[-500:], f, ensure_ascii=False, indent=4)

    bot.send_message(chat_id, f"RSS feed '{name}' has been successfully added.")
    choose_operation(chat_id)


def delete_link_by_name(message):
    chat_id = message.chat.id
    log_message(message)
    name = message.text.strip()

    # Load existing links
    with open(f"{DATA_DIR}/{chat_id}.json", 'r', encoding='utf-8') as f:
        scraped_links = json.load(f)

    # Find and delete the link by name
    new_links = [link for link in scraped_links if link["name"] != name]

    # Save updated links
    with open(f"{DATA_DIR}/{chat_id}.json", 'w', encoding='utf-8') as f:
        json.dump(new_links, f, ensure_ascii=False, indent=4)

    bot.send_message(chat_id, f"RSS feed '{name}' has been successfully deleted.")
    choose_operation(chat_id)


def process_rss_links(chat_id):
    # Check if this is the first run for the user
    first_run_flag_path = f"{DATA_DIR}/{chat_id}{FIRST_RUN_FILE_SUFFIX}"
    is_first_run = not os.path.exists(first_run_flag_path)

    # Load existing links
    with open(f"{DATA_DIR}/{chat_id}.json", 'r', encoding='utf-8') as f:
        scraped_links = json.load(f)

    # Load previously scraped entries to check for new ones
    scraped_entries = load_scraped_entries(chat_id)

    new_entries = []

    for link in scraped_links:
        feed = feedparser.parse(link["url"])
        for entry in feed.entries:
            entry_link = entry.link
            title = entry.title
            summary = entry.summary
            skills, country, budget = extract_details(summary)

            # Check if this entry has already been scraped
            if entry_link not in scraped_entries:
                new_entries.append(
                    f"New entry:\nTitle: {title}\nLink: {entry_link}\nSkills: {', '.join(skills)}\nCountry: {country}\nBudget: {budget}")
                scraped_entries.add(entry_link)

    # Save the updated set of scraped entries
    save_scraped_entries(chat_id, scraped_entries)

    if new_entries:
        # Send messages in batches
        if is_first_run:
            # Create the file to mark that the bot has run before for this user
            open(first_run_flag_path, 'w').close()

            # No messages sent on the first run
            return
        send_messages_in_batches(chat_id, new_entries)

    else:
        # Optionally, you could send a "No new entries" message if needed
        # bot.send_message(chat_id, "No new entries found.")
        pass

    choose_operation(chat_id, "silent")


def send_messages_in_batches(chat_id, messages, batch_size=10):
    for i in range(0, len(messages), batch_size):
        batch = messages[i:i + batch_size]
        message_text = "\n\n".join(batch)
        try:
            bot.send_message(chat_id, message_text)
            time.sleep(1)  # Adding a delay to avoid hitting API limits
        except telebot.apihelper.ApiException as e:
            print(f"Error sending message batch: {e}")
            time.sleep(5)  # Backoff in case of rate limit issues


def check_rss(url):
    try:
        feed = feedparser.parse(url)
        if 'upwork.com' not in feed.feed.link:
            return None
        return feed
    except Exception as e:
        print(f"Error parsing RSS feed: {e}")
        return None


def extract_details(summary):
    # Extract skills
    skills = re.findall(r'<b>Skills</b>:(.*?)<br />', summary, re.DOTALL)
    if skills:
        skills = skills[0].strip().replace('\n', '').replace(' ', '').split(',')
        skills = [skill.strip() for skill in skills if skill]
    else:
        skills = []

    # Extract country
    country_match = re.search(r'<b>Country</b>:\s*(.*?)<br />', summary)
    country = country_match.group(1).strip() if country_match else 'N/A'

    # Extract budget or hourly range
    budget_match = re.search(r'<b>Budget</b>:\s*\$?([\d,]+)', summary)
    hourly_range_match = re.search(r'<b>Hourly Range</b>:\s*\$([\d,.]+)-\$([\d,.]+)', summary)

    if budget_match:
        budget = budget_match.group(1).strip()
    elif hourly_range_match:
        hourly_min = hourly_range_match.group(1).strip()
        hourly_max = hourly_range_match.group(2).strip()
        budget = f"{hourly_min}-${hourly_max} per hour"
    else:
        budget = 'N/A'

    return skills, country, budget


def has_saved_links(chat_id):
    return os.path.exists(f"{DATA_DIR}/{chat_id}.json")


def get_saved_link_names(chat_id):
    with open(f"{DATA_DIR}/{chat_id}.json", 'r', encoding='utf-8') as f:
        scraped_links = json.load(f)
    return "\n".join(link["name"] for link in scraped_links)


def load_scraped_entries(chat_id):
    entries_file = f"{DATA_DIR}/{chat_id}_entries.json"
    if os.path.exists(entries_file):
        with open(entries_file, 'r', encoding='utf-8') as f:
            return set(json.load(f))
    return set()


def save_scraped_entries(chat_id, entries):
    entries_file = f"{DATA_DIR}/{chat_id}_entries.json"
    with open(entries_file, 'w', encoding='utf-8') as f:
        json.dump(list(entries), f, ensure_ascii=False, indent=4)


def choose_operation(chat_id, silent=None):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("/delete Delete", "/add Add", "/run Start check", "/break_run Stop check")
    if silent is None:
        bot.send_message(chat_id, "Choose a command:", reply_markup=markup)


def start_periodic_check(chat_id):
    # Check if the periodic check is already running
    running_flag_path = f"{DATA_DIR}/{chat_id}{RUNNING_THREAD_SUFFIX}"
    if os.path.exists(running_flag_path):
        bot.send_message(chat_id, "The checking process is already running.")
        return

    # Create the running flag file
    open(running_flag_path, 'w').close()
    bot.send_message(chat_id, "Starting periodic RSS feed check...")

    def periodic_check():
        while os.path.exists(running_flag_path):
            process_rss_links(chat_id)
            time.sleep(60)  # Wait for 1 minute before next check

    # Start the periodic check in a new thread
    thread = threading.Thread(target=periodic_check)
    thread.start()


def stop_periodic_check(chat_id):
    running_flag_path = f"{DATA_DIR}/{chat_id}{RUNNING_THREAD_SUFFIX}"
    if os.path.exists(running_flag_path):
        os.remove(running_flag_path)
        bot.send_message(chat_id, "Periodic check has been stopped.")
    else:
        bot.send_message(chat_id, "The checking process is not running.")


# Start the bot
bot.polling(none_stop=True)
