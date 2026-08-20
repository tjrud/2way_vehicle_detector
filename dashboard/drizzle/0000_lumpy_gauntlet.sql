CREATE TABLE `members` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`email` text NOT NULL,
	`provider` text NOT NULL,
	`role` text DEFAULT 'user' NOT NULL,
	`created_at` integer NOT NULL,
	`last_seen_at` integer NOT NULL,
	`login_count` integer DEFAULT 0 NOT NULL
);
--> statement-breakpoint
CREATE TABLE `usage_events` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`member_id` text NOT NULL,
	`member_name` text NOT NULL,
	`member_email` text NOT NULL,
	`event_type` text NOT NULL,
	`detail` text NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE INDEX `idx_usage_events_created_at` ON `usage_events` (`created_at`);--> statement-breakpoint
CREATE INDEX `idx_usage_events_member_id_created_at` ON `usage_events` (`member_id`,`created_at`);
--> statement-breakpoint
PRAGMA optimize;
