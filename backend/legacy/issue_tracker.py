from jira import JIRA
from dotenv import load_dotenv
import os

load_dotenv()

JIRA_SERVER = os.getenv('JIRA_SERVER')
JIRA_USERNAME = os.getenv('JIRA_USERNAME')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN')

class IssueTracker:
    def __init__(self):
        # 1. Connect to Jira Cloud
        self.jql_jira = JIRA(
            server=JIRA_SERVER,
            basic_auth=(JIRA_USERNAME, JIRA_API_TOKEN)  #
        )

    def format_issue_description(self, issue):
        return f"[{issue.key}] {issue.fields.summary} | Status: {issue.fields.status.name} | Requester Email: {issue.raw["fields"].get("customfield_10062")}"

    def fetch_open_tickets(self, requester_email):
        # Define JQL to get open statuses (e.g., 'To Do', 'In Progress')
        escaped_email = requester_email.replace("@", r"\u0040")
        jql_query = f'project = "SUP" AND "Requester Email" = {escaped_email} ORDER BY created DESC'

        # Search and iterate over results
        print("Fetching open tickets...")
        issues = self.jql_jira.search_issues(jql_query, maxResults=50, fields="summary,customfield_10062,status")  #

        return [self.format_issue_description(issue) for issue in issues]

    def create_new_ticket(self, username, ticket_title, ticket_description):
        # Define your issue details
        issue_data = {
            "project": {"key": "SUP"},  # Your project key
            "summary": f"Bug: {ticket_title}",  # Ticket title
            "description": ticket_description,
            "issuetype": {"name": "Task"},  # 'Task', 'Story', 'Bug', etc.
            "customfield_10062": username
        }

        # Create the ticket
        try:
            # print("Creating new ticket...")
            new_issue = self.jql_jira.create_issue(fields=issue_data)  #
            print(f"Ticket successfully opened!: {self.format_issue_description(new_issue)}")  #
        except Exception as e:
            print(f"Failed to open ticket: {e}")  #


# if __name__ == "__main__":
#     issue_tracker = IssueTracker()
#     issue_tracker.create_new_ticket("abhi@example.com", "Test Ticket 5", "Test Ticket Description 5")
#     issues = issue_tracker.fetch_open_tickets("abhi@example.com")
#     print(issues)
