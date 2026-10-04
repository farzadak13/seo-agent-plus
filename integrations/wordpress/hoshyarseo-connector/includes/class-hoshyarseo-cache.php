<?php
/**
 * Purges cached copies of a page after its title changes.
 *
 * HoshyarSEO checks a change by reading the public page. A page cache that
 * still serves the old HTML would make a correct change look failed, and
 * would show visitors and Google the old title until it expired.
 *
 * @package HoshyarSEO_Connector
 */

defined( 'ABSPATH' ) || exit;

/**
 * Best-effort cache purge across common caching plugins.
 */
class HoshyarSEO_Cache {

	/**
	 * Purge every cache we know how to reach for one post.
	 *
	 * @param int $post_id Post ID.
	 * @return string[] Names of the caches that were purged.
	 */
	public function purge( $post_id ) {
		$post_id = absint( $post_id );
		$purged  = array( 'object' );
		clean_post_cache( $post_id );

		if ( has_action( 'litespeed_purge_post' ) ) {
			do_action( 'litespeed_purge_post', $post_id );
			$purged[] = 'litespeed';
		}
		if ( function_exists( 'rocket_clean_post' ) ) {
			rocket_clean_post( $post_id );
			$purged[] = 'wp-rocket';
		}
		if ( function_exists( 'w3tc_flush_post' ) ) {
			w3tc_flush_post( $post_id );
			$purged[] = 'w3-total-cache';
		}
		if ( function_exists( 'wp_cache_post_change' ) ) {
			wp_cache_post_change( $post_id );
			$purged[] = 'wp-super-cache';
		}
		if ( has_action( 'sg_cachepress_purge_url' ) ) {
			do_action( 'sg_cachepress_purge_url', get_permalink( $post_id ) );
			$purged[] = 'siteground';
		}

		/**
		 * Lets a site purge a cache this plugin does not know about.
		 *
		 * @param int $post_id Post whose title changed.
		 */
		do_action( 'hoshyarseo_title_changed', $post_id );

		return $purged;
	}
}
