<?php
/**
 * Title overrides and the history needed to undo them exactly.
 *
 * The override lives in its own post meta key. Nothing belonging to the SEO
 * plugin or to the post itself is modified, so removing the override returns
 * the page to whatever the site would render without HoshyarSEO.
 *
 * @package HoshyarSEO_Connector
 */

defined( 'ABSPATH' ) || exit;

/**
 * Stores, reads and reverts title overrides.
 */
class HoshyarSEO_Title_Store {

	const META_TITLE   = '_hoshyarseo_title';
	const META_HISTORY = '_hoshyarseo_title_history';
	const HISTORY_SIZE = 20;
	const MAX_LENGTH   = 300;

	/**
	 * Current override for a post, or null when none is set.
	 *
	 * @param int $post_id Post ID.
	 * @return string|null
	 */
	public function get( $post_id ) {
		$value = get_post_meta( absint( $post_id ), self::META_TITLE, true );
		return ( is_string( $value ) && '' !== $value ) ? $value : null;
	}

	/**
	 * Set an override and record what it replaced.
	 *
	 * Idempotent per key: a request repeated with the same idempotency key
	 * returns the change it already made instead of making a second one.
	 *
	 * @param int    $post_id         Post ID.
	 * @param string $title           New title (plain text).
	 * @param string $idempotency_key Caller-supplied key, unique per change.
	 * @return array|WP_Error The change record.
	 */
	public function apply( $post_id, $title, $idempotency_key ) {
		$post_id = absint( $post_id );
		$title   = $this->clean( $title );
		$key     = sanitize_text_field( $idempotency_key );

		if ( '' === $title ) {
			return new WP_Error( 'hoshyarseo_empty_title', __( 'The title must not be empty.', 'hoshyarseo-connector' ), array( 'status' => 422 ) );
		}
		if ( mb_strlen( $title ) > self::MAX_LENGTH ) {
			return new WP_Error( 'hoshyarseo_long_title', __( 'The title is too long.', 'hoshyarseo-connector' ), array( 'status' => 422 ) );
		}
		if ( '' === $key ) {
			return new WP_Error( 'hoshyarseo_missing_key', __( 'An idempotency key is required.', 'hoshyarseo-connector' ), array( 'status' => 422 ) );
		}

		$history = $this->history( $post_id );
		foreach ( $history as $entry ) {
			if ( isset( $entry['idempotency_key'] ) && $entry['idempotency_key'] === $key ) {
				return $entry;
			}
		}

		$entry = array(
			'change_id'       => $post_id . ':' . wp_generate_uuid4(),
			'idempotency_key' => $key,
			'previous'        => $this->get( $post_id ),
			'new'             => $title,
			'at'              => gmdate( 'c' ),
			'rolled_back'     => false,
		);

		update_post_meta( $post_id, self::META_TITLE, $title );
		$history[] = $entry;
		$this->save_history( $post_id, $history );

		return $entry;
	}

	/**
	 * Undo one change, restoring exactly what was there before it.
	 *
	 * Refuses when the override has changed since: undoing an older change
	 * would discard a newer one.
	 *
	 * @param int    $post_id   Post ID.
	 * @param string $change_id Change ID returned by apply().
	 * @return array|WP_Error The updated change record.
	 */
	public function rollback( $post_id, $change_id ) {
		$post_id   = absint( $post_id );
		$change_id = sanitize_text_field( $change_id );
		$history   = $this->history( $post_id );

		foreach ( $history as $index => $entry ) {
			if ( $entry['change_id'] !== $change_id ) {
				continue;
			}
			if ( ! empty( $entry['rolled_back'] ) ) {
				return $entry;
			}
			if ( $this->get( $post_id ) !== $entry['new'] ) {
				return new WP_Error( 'hoshyarseo_changed_since', __( 'The title was changed after this change; it was not rolled back.', 'hoshyarseo-connector' ), array( 'status' => 409 ) );
			}

			if ( null === $entry['previous'] ) {
				delete_post_meta( $post_id, self::META_TITLE );
			} else {
				update_post_meta( $post_id, self::META_TITLE, $entry['previous'] );
			}

			$history[ $index ]['rolled_back']    = true;
			$history[ $index ]['rolled_back_at'] = gmdate( 'c' );
			$this->save_history( $post_id, $history );
			return $history[ $index ];
		}

		return new WP_Error( 'hoshyarseo_unknown_change', __( 'No such change for this page.', 'hoshyarseo-connector' ), array( 'status' => 404 ) );
	}

	/**
	 * Change history for a post, oldest first.
	 *
	 * @param int $post_id Post ID.
	 * @return array
	 */
	public function history( $post_id ) {
		$history = get_post_meta( absint( $post_id ), self::META_HISTORY, true );
		return is_array( $history ) ? $history : array();
	}

	/**
	 * Persist history, keeping only the most recent entries.
	 *
	 * @param int   $post_id Post ID.
	 * @param array $history Entries, oldest first.
	 * @return void
	 */
	private function save_history( $post_id, array $history ) {
		update_post_meta( $post_id, self::META_HISTORY, array_slice( $history, -self::HISTORY_SIZE ) );
	}

	/**
	 * Plain text, single-spaced. Titles carry no markup.
	 *
	 * @param string $title Raw title.
	 * @return string
	 */
	private function clean( $title ) {
		$title = sanitize_text_field( (string) $title );
		return trim( preg_replace( '/\s+/u', ' ', $title ) );
	}
}
